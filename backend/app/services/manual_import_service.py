"""The manual reference import: records that have no provider.

A validation study scores nights against instruments no wearable platform serves -
a PSG export, a Kubios analysis of a Polar H10, an EEG headband's file. For Open
Wearables to stay the only measurement store, those records need a way in that
lands them beside the devices they score, with the same attribution contract: a
`user_connection` (provider `manual`, classified `reference`), one `data_source`
per instrument, and the ordinary sleep and time-series tables.

What this module does not do is interpret. A stage label the instrument used is
stored verbatim (`vendor_stage`); only a label that is already one of OW's words is
also filed as `stage`, and totals are computed only when every label was. A foreign
alphabet gets null totals, never figures derived from a fold the consumer owns.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from app.config import settings
from app.constants.sleep import SleepStageType
from app.database import DbSession
from app.models import DataSource, UserConnection
from app.repositories.data_source_repository import DataSourceRepository
from app.schemas.enums import AccountType, EventCategory, ProviderName, SampleRoute
from app.schemas.manual_import import (
    ManualSeriesImport,
    ManualSeriesImportResult,
    ManualSleepImportResult,
    ManualSleepStagesImport,
)
from app.schemas.model_crud.activities import (
    EventRecordCreate,
    EventRecordDetailCreate,
    SleepStage,
    TimeSeriesSampleCreate,
)
from app.services.event_record_service import event_record_service
from app.services.timeseries_service import timeseries_service
from app.services.user_connection_service import user_connection_service
from app.services.user_service import user_service
from app.utils.dates import offset_to_iso
from app.utils.structured_logging import StructuredLogger, get_logger

MANUAL_PROVIDER = ProviderName.MANUAL.value
REFERENCE_ACCOUNT_LABEL = "Reference instruments (manual import)"

# The sleep stages that count toward total sleep, as the SDK path counts them.
_ASLEEP = (SleepStageType.SLEEPING, SleepStageType.LIGHT, SleepStageType.DEEP, SleepStageType.REM)


class ManualConnectionNotFoundError(LookupError):
    """The named connection is not a `manual` connection of this user (the route maps it to 404)."""


def _canonical_stage(label: str) -> SleepStageType:
    """OW's word for the label if it already is one; otherwise `unknown`, with the label kept beside it."""
    try:
        return SleepStageType(label.strip().lower())
    except ValueError:
        return SleepStageType.UNKNOWN


def _offset_of(moment: datetime) -> str | None:
    offset = moment.utcoffset()
    return offset_to_iso(int(offset.total_seconds())) if offset is not None else None


def _metadata(provenance: str | None, **stated: object) -> dict[str, object] | None:
    """`provider_metadata` for a manual write: the importer's batch tag and what it stated, or NULL."""
    metadata = {key: value for key, value in stated.items() if value is not None}
    if provenance is not None:
        metadata["provenance"] = provenance
    return metadata or None


def _clipped_seconds(stages: list[SleepStage]) -> dict[SleepStageType, float]:
    """Seconds per stage over a sorted timeline, overlaps clipped to the earlier interval."""
    seconds: dict[SleepStageType, float] = {}
    last_end: datetime | None = None
    for stage in stages:
        start = max(stage.start_time, last_end) if last_end is not None else stage.start_time
        if start >= stage.end_time:
            continue
        seconds[stage.stage] = seconds.get(stage.stage, 0.0) + (stage.end_time - start).total_seconds()
        last_end = stage.end_time
    return seconds


class ManualImportService:
    def __init__(self, log: StructuredLogger) -> None:
        self.logger = log
        self.data_source_repo = DataSourceRepository(DataSource)

    def resolve_connection(self, db_session: DbSession, user_id: UUID, connection_id: UUID | None) -> UserConnection:
        """The `manual` connection to file under: the one named, or the user's own, created if absent.

        A fresh connection is classified `reference` because that is what a record with no
        provider is in a validation study; a person can reclassify it afterwards and the
        import will not put it back. An id that names another user's connection, or a
        connection of another provider, is refused rather than resolved to something else.
        """
        user_service.get(db_session, user_id, raise_404=True)
        if connection_id is None:
            return user_connection_service.ensure_sdk_connection(
                db_session,
                user_id,
                MANUAL_PROVIDER,
                account_type=AccountType.REFERENCE.value,
                account_label=REFERENCE_ACCOUNT_LABEL,
            )
        connection = user_connection_service.get_account(db_session, user_id, connection_id)
        if connection is None or connection.provider != MANUAL_PROVIDER:
            raise ManualConnectionNotFoundError(f"No manual connection {connection_id} for this user")
        return connection

    def _data_source_of(self, db_session: DbSession, user_id: UUID, connection: UserConnection, label: str) -> UUID:
        source = self.data_source_repo.get_by_connection_identity(
            db_session, user_id, ProviderName.MANUAL, device_model=label, source=label, user_connection_id=connection.id
        )
        assert source is not None, "the write that just happened created this data source"
        return source.id

    def import_sleep_stages(
        self, db_session: DbSession, user_id: UUID, payload: ManualSleepStagesImport
    ) -> ManualSleepImportResult:
        """One sleep record with its stage intervals, stored as the Apple Health relay stores them.

        Written through ``create_or_merge_sleep`` like every provider's night, so a
        re-import with the same ``external_id`` replaces the record in place and two
        fragments of one night on one instrument join. The instrument's label is the
        data source's ``source``, which the adjacency lookup scopes on, so the same
        night from a second instrument stays a second record.
        """
        connection = self.resolve_connection(db_session, user_id, payload.connection_id)
        label = payload.device_label.strip()
        stages = sorted(payload.stages, key=lambda s: (s.start_time, s.end_time))
        start = payload.sleep_start or stages[0].start_time
        end = payload.sleep_end or max(s.end_time for s in stages)
        if end <= start:
            raise ValueError("the record ends before it starts")
        in_bed_minutes = int((end - start).total_seconds() // 60)

        stored = [
            SleepStage(
                stage=_canonical_stage(s.stage), start_time=s.start_time, end_time=s.end_time, vendor_stage=s.stage
            )
            for s in stages
        ]
        stated = payload.totals
        if stated is not None:
            # As the instrument stated them; a stage it did not state stays null, never zero.
            totals_scored = True
            asleep_minutes: int | None = stated.total_sleep_minutes
            deep, light, rem, awake = (
                stated.deep_minutes,
                stated.light_minutes,
                stated.rem_minutes,
                stated.awake_minutes,
            )
            efficiency = (
                Decimal(str(round(stated.total_sleep_minutes / in_bed_minutes * 100, 2))) if in_bed_minutes else None
            )
        else:
            # Totals only from a timeline that is entirely in OW's words. A foreign label
            # leaves every total null: a figure summed over a fold this store did not make
            # would be a synthesised measurement, and null is not zero.
            totals_scored = all(s.stage is not SleepStageType.UNKNOWN for s in stored)
            seconds = _clipped_seconds(stored) if totals_scored else None

            def minutes(stage: SleepStageType) -> int | None:
                return int(seconds.get(stage, 0.0) // 60) if seconds is not None else None

            asleep_seconds = sum(seconds.get(stage, 0.0) for stage in _ASLEEP) if seconds is not None else None
            asleep_minutes = int(asleep_seconds // 60) if asleep_seconds is not None else None
            deep, light, rem, awake = (
                minutes(SleepStageType.DEEP),
                minutes(SleepStageType.LIGHT),
                minutes(SleepStageType.REM),
                minutes(SleepStageType.AWAKE),
            )
            efficiency = (
                Decimal(str(round(asleep_seconds / (in_bed_minutes * 60) * 100, 2)))
                if asleep_seconds is not None and in_bed_minutes
                else None
            )

        metadata = _metadata(payload.provenance, totals="stated" if stated is not None else None)
        external_id = payload.external_id or f"manual:{label}:{start.isoformat()}"
        record = EventRecordCreate(
            id=uuid4(),
            external_id=external_id,
            user_id=user_id,
            provider=MANUAL_PROVIDER,
            user_connection_id=connection.id,
            category=EventCategory.SLEEP,
            type="sleep_session",
            source_name=label,
            source=label,
            device_model=label,
            start_datetime=start,
            end_datetime=end,
            duration_seconds=int((end - start).total_seconds()),
            zone_offset=payload.zone_offset or _offset_of(start),
            provider_metadata=metadata,
        )
        detail = EventRecordDetailCreate(
            record_id=record.id,
            sleep_time_in_bed_minutes=in_bed_minutes,
            sleep_total_duration_minutes=asleep_minutes,
            sleep_deep_minutes=deep,
            sleep_rem_minutes=rem,
            sleep_light_minutes=light,
            sleep_awake_minutes=awake,
            sleep_efficiency_score=efficiency,
            is_nap=False,
            sleep_stages=stored or None,
        )
        result = event_record_service.create_or_merge_sleep(
            db_session, user_id, record, detail, settings.sleep_end_gap_minutes
        )
        # A replaced or merged record keeps the row it already had, metadata included;
        # this write's tags go on it so the record says what last wrote it.
        if metadata is not None and result.provider_metadata != {**(result.provider_metadata or {}), **metadata}:
            result.provider_metadata = {**(result.provider_metadata or {}), **metadata}
            db_session.commit()
        self.logger.info(
            "Imported manual sleep record",
            provider=MANUAL_PROVIDER,
            action="manual_sleep_import",
            user_id=str(user_id),
            connection_id=str(connection.id),
            record_id=str(result.id),
            stage_count=len(stored),
            totals_scored=totals_scored,
            totals_source="stated" if stated is not None else "stages",
            provenance=payload.provenance,
            replaced=result.id != record.id,
        )
        return ManualSleepImportResult(
            record_id=result.id,
            connection_id=connection.id,
            data_source_id=result.data_source_id,
            external_id=external_id,
            start_time=result.start_datetime,
            end_time=result.end_datetime,
            stage_count=len(stored),
            replaced=result.id != record.id,
            totals_scored=totals_scored,
            totals_source="stated" if stated is not None else "stages",
        )

    def import_series(
        self, db_session: DbSession, user_id: UUID, payload: ManualSeriesImport
    ) -> ManualSeriesImportResult:
        """Time-series rows through the ordinary bulk upsert, so a re-import is a no-op.

        Rows are keyed on (data source, series type, second); the importer's instrument
        label is the data source, so the same file sent twice changes nothing and a
        corrected value replaces the old one. A declared window goes on every row
        (`interval_seconds`) and files the rows under the window-kind feed, which is
        what ranks them against a point sample on the same second.
        """
        connection = self.resolve_connection(db_session, user_id, payload.connection_id)
        label = payload.device_label.strip()
        route = SampleRoute.MANUAL_WINDOWS if payload.interval_seconds else SampleRoute.MANUAL_SAMPLES
        metadata = _metadata(payload.provenance, interval_seconds=payload.interval_seconds)
        creators = [
            TimeSeriesSampleCreate(
                route=route,
                id=uuid4(),
                user_id=user_id,
                provider=MANUAL_PROVIDER,
                user_connection_id=connection.id,
                source=label,
                device_model=label,
                recorded_at=sample.recorded_at,
                zone_offset=payload.zone_offset or _offset_of(sample.recorded_at),
                value=sample.value,
                series_type=payload.series_type,
                provider_metadata=metadata,
            )
            for sample in payload.samples
        ]
        counts = timeseries_service.bulk_create_samples(db_session, creators)
        db_session.commit()
        self.logger.info(
            "Imported manual series",
            provider=MANUAL_PROVIDER,
            action="manual_series_import",
            user_id=str(user_id),
            connection_id=str(connection.id),
            series=payload.series_type.value,
            received=len(creators),
            inserted=counts.inserted,
            updated=counts.updated,
            provenance=payload.provenance,
        )
        return ManualSeriesImportResult(
            connection_id=connection.id,
            data_source_id=self._data_source_of(db_session, user_id, connection, label),
            series_type=payload.series_type,
            route=route.value,
            received=len(creators),
            inserted=counts.inserted,
            updated=counts.updated,
        )


manual_import_service = ManualImportService(log=get_logger(__name__))
