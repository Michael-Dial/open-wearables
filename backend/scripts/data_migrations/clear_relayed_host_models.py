#!/usr/bin/env python3
"""Clear the relaying iPhone's model from third-party HealthKit data sources.

iOS SDKs reported ``sourceRevision.productType`` as the model of every record, which for a
third-party writer (Oura, Zepp Life, ...) is the iPhone or Watch the app ran on, not the device
that produced the data. Its device type was inferred from that code too, so e.g. an Oura ring
synced through Apple Health was stored as a phone. Ingest now drops that code, and the iOS SDK
sends the writer's own ``HKDevice.model`` instead; this clears it from rows stored before.

Only app-keyed rows are touched: without ``source_app_id`` a relayed row cannot be told apart
from the iPhone's own, and live sync re-keys and clears those on the writer's next record.
The model is set to NULL and the device type re-resolved from the writer's name. A row is
skipped when the writer already has a source without a model, since clearing it would collide
with that row.

Fork: matched on the fork's identity, not upstream's. ``provider_device_id`` is what upstream calls
``device_id`` (the fork's ``device_id`` is the device-registry link, unrelated here), and a writer's
rows are grouped per account, since ``user_connection_id`` is part of every identity tier. The
model's origin is cleared with it (``ck_data_source_device_model_origin`` allows no origin without
a model), a model a label put there is left alone, and the type is re-resolved by the classifier
live sync uses (``DataSourceRepository._infer_device_type``), so a sensor declared on the account
still decides it; a writer whose name says nothing (MyFitnessPal) keeps its stored type.

Runs once per instance (``data_migration``).

Usage (inside Docker):
    docker compose exec app uv run python scripts/data_migrations/clear_relayed_host_models.py --dry-run
    docker compose exec app uv run python scripts/data_migrations/clear_relayed_host_models.py
"""

import argparse
from collections import Counter
from uuid import UUID

from sqlalchemy.orm import Session

from app.constants.devices_map import is_host_model
from app.database import engine
from app.models import DataSource
from app.repositories.data_source_repository import DataSourceRepository
from app.schemas.enums import DeviceModelOrigin, DeviceType, ProviderName
from app.services.data_migration_service import data_migration_service


def clear_relayed_host_models(db: Session, *, dry_run: bool) -> Counter[str]:
    """Does not commit; the caller owns the transaction.

    Returns counts keyed by ``"cleared"`` and ``"skipped"``.
    """
    rows = (
        db.query(DataSource)
        .filter(
            DataSource.provider == ProviderName.APPLE,
            DataSource.source_app_id.isnot(None),
            DataSource.provider_device_id.is_(None),
        )
        .all()
    )
    repo = DataSourceRepository()
    declared: dict[tuple[UUID, UUID | None], str | None] = {}
    without_model = {(r.user_id, r.user_connection_id, r.source_app_id) for r in rows if r.device_model is None}
    counts: Counter[str] = Counter()
    for ds in rows:
        if not is_host_model(ds.provider, ds.source_app_id, ds.device_model):
            continue
        if ds.device_model_origin == DeviceModelOrigin.LABEL.value:
            continue
        writer = (ds.user_id, ds.user_connection_id, ds.source_app_id)
        if writer in without_model:
            counts["skipped"] += 1
            continue
        account = (ds.user_id, ds.user_connection_id)
        if account not in declared:
            declared[account] = repo._connection_sensor_label(db, ds.user_id, ProviderName.APPLE, ds.user_connection_id)
        resolved = repo._infer_device_type(
            None, ds.original_source_name, ds.source, declared[account], provider=ProviderName.APPLE
        )
        # A resolution of unknown never erases a type (fork rule; MyFitnessPal on the phone is a phone)
        new_type = resolved.value if resolved is not DeviceType.UNKNOWN else ds.device_type or resolved.value
        print(f"{ds.source_app_id}: {ds.device_model} ({ds.device_type}) -> NULL ({new_type})")
        counts["cleared"] += 1
        if not dry_run:
            ds.device_model = None
            ds.device_model_origin = None
            ds.device_type = new_type
        without_model.add(writer)

    verb = "Would clear" if dry_run else "Cleared"
    print(
        f"{verb} the relaying host model on {counts['cleared']} data source(s)"
        + (
            f"; skipped {counts['skipped']} whose writer already has a source without a model"
            if counts["skipped"]
            else ""
        )
    )
    if dry_run:
        print("\nDry run — no changes made.")
    return counts


def _migrate(db: Session, dry_run: bool) -> bool:
    clear_relayed_host_models(db, dry_run=dry_run)
    if dry_run:
        return False
    db.commit()
    return True


def main(dry_run: bool) -> None:
    with engine.connect() as connection, Session(bind=connection) as db:
        data_migration_service.run(db, "clear_relayed_host_models", lambda: _migrate(db, dry_run), dry_run=dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview affected rows without modifying data")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
