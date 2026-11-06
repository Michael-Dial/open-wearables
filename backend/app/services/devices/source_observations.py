"""Record what each sync said about the hardware and software behind its data sources.

The SDK route describes a source with HealthKit's or Health Connect's fields; a cloud
provider route records what its own payload states (Oura: the ``ring_id`` on each sleep).

``data_source`` keeps one current value of the few fields it is keyed or typed by. The
rest of what HealthKit and Health Connect attach to every record - the accessory's own
name, its manufacturer, hardware, firmware and software versions, the writing app's
version, the phone's OS - was parsed and dropped. This pass keeps each distinct
combination per data source, dated by the records that carried it, in
``data_source_observation``.

It runs once per sync after the data is stored, like the identity pass beside it, and is
best effort for the same reason: provenance must never cost a sync its data.
"""

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from app.database import DbSession
from app.models import DataSourceObservation
from app.services.devices.sdk_identities import find_sdk_data_source
from app.services.sdk.device_resolution import extract_device_ids, extract_device_info
from app.utils.structured_logging import get_logger

log = get_logger(__name__)

# Column -> how to read it off a SourceInfo. Order is the fingerprint's order, so it is
# append-only: reordering would re-fingerprint every stored row as new.
_FIELDS: tuple[tuple[str, str], ...] = (
    ("source_name", "name"),
    ("source_bundle_id", "bundle_identifier"),
    ("source_version", "version"),
    ("source_product_type", "product_type"),
    ("source_os_version", "operating_system_version"),
    ("device_name", "device_name"),
    ("device_manufacturer", "device_manufacturer"),
    ("device_model", "device_model"),
    ("device_hardware_version", "device_hardware_version"),
    ("device_firmware_version", "device_firmware_version"),
    ("device_software_version", "device_software_version"),
    ("device_local_identifier", "device_local_identifier"),
    ("device_udi", "device_udi"),
    ("device_type_reported", "device_type"),
    ("recording_method", "recording_method"),
)

# Columns added after the first release. They enter the fingerprint only when set, so a row
# stored before a column existed keeps its fingerprint and is not duplicated by the next sync.
_LATER_FIELDS: tuple[str, ...] = ("device_serial_number",)

_COLUMNS: tuple[str, ...] = tuple(c for c, _ in _FIELDS) + _LATER_FIELDS

_MAX_LEN = 255


def _text(value: Any) -> str | None:
    """A stored string for one field: trimmed, capped, and None for absent or blank."""
    if value is None:
        return None
    value = getattr(value, "value", value)
    if all(hasattr(value, a) for a in ("major_version", "minor_version", "patch_version")):
        value = f"{value.major_version}.{value.minor_version}.{value.patch_version}"
    text = str(value).strip()
    return text[:_MAX_LEN] or None


def observation_values(source: Any) -> dict[str, str | None]:
    """Every provenance field a SourceInfo carries, as stored. The writer id falls back to appId."""
    values = {column: _text(getattr(source, attr, None)) for column, attr in _FIELDS}
    if values["source_bundle_id"] is None:
        values["source_bundle_id"] = _text(getattr(source, "app_id", None))
    return values


def fingerprint(values: dict[str, str | None]) -> str:
    parts: list[object] = [values.get(c) for c, _ in _FIELDS]
    parts += [[c, values[c]] for c in _LATER_FIELDS if values.get(c) is not None]
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


@dataclass
class _Group:
    values: dict[str, str | None]
    first: datetime | None = None
    last: datetime | None = None
    count: int = 0

    def add(self, start: datetime | None, end: datetime | None) -> None:
        self.count += 1
        for moment in (start, end):
            if moment is None:
                continue
            if self.first is None or moment < self.first:
                self.first = moment
            if self.last is None or moment > self.last:
                self.last = moment

    def absorb(self, other: "_Group") -> None:
        self.count += other.count
        for moment in (other.first, other.last):
            if moment is not None:
                self.first = moment if self.first is None or moment < self.first else self.first
                self.last = moment if self.last is None or moment > self.last else self.last


def record_sdk_observations(
    db_session: DbSession,
    user_id: UUID,
    provider: str,
    records: Iterable[tuple[Any, datetime | None, datetime | None]],
) -> int:
    """Upsert one observation per (data source, distinct description). Returns rows written.

    ``records`` is each record's SourceInfo with its start and end. A source whose data
    source cannot be found (a record dropped before it was stored) is skipped: the
    observation describes stored data or nothing.
    """
    groups: dict[tuple, _Group] = {}
    for source, start, end in records:
        if source is None:
            continue
        values = observation_values(source)
        if not any(values.values()):
            continue
        device_model, _sw, original_source_name = extract_device_info(source, provider)
        provider_device_id, source_app_id, _manufacturer = extract_device_ids(source)
        key = (device_model, original_source_name, provider_device_id, source_app_id, fingerprint(values))
        group = groups.get(key)
        if group is None:
            group = groups[key] = _Group(values=values)
        group.add(start, end)

    if not groups:
        return 0

    resolved: dict[tuple[UUID, str], _Group] = {}
    try:
        for (device_model, source_name, provider_device_id, source_app_id, digest), group in groups.items():
            data_source = find_sdk_data_source(
                db_session, user_id, provider, device_model, source_name, provider_device_id, source_app_id
            )
            if data_source is None:
                continue
            # Two keys can name one data source; their records are one observation.
            key = (data_source.id, digest)
            if key in resolved:
                resolved[key].absorb(group)
            else:
                resolved[key] = group
    except Exception:
        db_session.rollback()
        log.exception("Failed to resolve SDK source observations", user_id=str(user_id), provider=provider)
        return 0
    return _upsert(db_session, user_id, resolved, provider=provider)


def blank_values() -> dict[str, str | None]:
    """Every observation column, unsaid. A provider route fills only what its payload states."""
    return {column: None for column in _COLUMNS}


def record_provider_observations(
    db_session: DbSession,
    user_id: UUID,
    provider: str,
    records: Iterable[tuple[UUID, dict[str, str | None], datetime | None, datetime | None]],
) -> int:
    """Upsert observations for a cloud provider route, whose records already name their data source.

    ``records`` is each stored record's data source id, its observation values (from
    ``blank_values``, filled with what the payload said) and its start and end. Same
    grouping, dating and best-effort rule as the SDK pass.
    """
    groups: dict[tuple[UUID, str], _Group] = {}
    try:
        for data_source_id, raw_values, start, end in records:
            values = {column: _text(raw_values.get(column)) for column in _COLUMNS}
            if not any(values.values()):
                continue
            key = (data_source_id, fingerprint(values))
            group = groups.get(key)
            if group is None:
                group = groups[key] = _Group(values=values)
            group.add(start, end)
    except Exception:
        # Provenance must never cost a sync its data, the grouping included.
        log.exception("Failed to group source observations", user_id=str(user_id), provider=provider)
        return 0
    return _upsert(db_session, user_id, groups, provider=provider)


def _upsert(db_session: DbSession, user_id: UUID, groups: dict[tuple[UUID, str], _Group], *, provider: str) -> int:
    if not groups:
        return 0
    now = datetime.now(UTC)
    written = 0
    try:
        for (data_source_id, digest), group in groups.items():
            obs = DataSourceObservation
            stmt = insert(obs).values(
                id=uuid4(),
                user_id=user_id,
                data_source_id=data_source_id,
                fingerprint=digest,
                first_recorded_at=group.first,
                last_recorded_at=group.last,
                first_received_at=now,
                last_received_at=now,
                records_reported=group.count,
                **group.values,
            )
            # LEAST/GREATEST ignore NULLs in Postgres, so a record with no time never
            # erases a span another record established.
            stmt = stmt.on_conflict_do_update(
                index_elements=[obs.data_source_id, obs.fingerprint],
                set_={
                    "first_recorded_at": func.least(obs.first_recorded_at, stmt.excluded.first_recorded_at),
                    "last_recorded_at": func.greatest(obs.last_recorded_at, stmt.excluded.last_recorded_at),
                    "last_received_at": stmt.excluded.last_received_at,
                    "records_reported": obs.records_reported + stmt.excluded.records_reported,
                },
            )
            db_session.execute(stmt)
            written += 1
        db_session.commit()
    except Exception:
        db_session.rollback()
        log.exception("Failed to record source observations", user_id=str(user_id), provider=provider)
        return 0
    return written
