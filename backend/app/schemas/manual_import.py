"""Bodies and results of the manual reference import (`/users/{id}/import/manual/*`)."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from app.schemas.enums import SeriesType
from app.utils.dates import ZoneOffset

DeviceLabel = Annotated[
    str,
    Field(
        min_length=1,
        max_length=100,
        description=(
            'The instrument, as a person names it ("PSG - Compumedics Grael", "Polar H10 via Kubios", '
            '"Muse S"). Becomes the data source\'s device_model and source, so two instruments are two '
            "sources and a night from one never merges with the same night from the other."
        ),
    ),
]
ConnectionId = Annotated[
    UUID | None,
    Field(
        description=(
            "The user's `manual` connection to file under. Omitted: the user's existing manual "
            "connection, created with account_type `reference` if there is none yet."
        ),
    ),
]


class ManualSleepStageIn(BaseModel):
    stage: str = Field(
        min_length=1,
        max_length=32,
        description=(
            "The instrument's own label, kept verbatim as `vendor_stage`. One of OW's words (in_bed, awake, "
            "sleeping, light, deep, rem, unknown; case-insensitive) is also stored as `stage`; any other "
            "label (N1, N2, N3, W, R, ...) is stored with `stage` = unknown and is folded by the consumer, "
            "never here."
        ),
    )
    start_time: AwareDatetime
    end_time: AwareDatetime

    @model_validator(mode="after")
    def _ends_after_it_starts(self) -> "ManualSleepStageIn":
        if self.end_time <= self.start_time:
            raise ValueError("a stage must end after it starts")
        return self


class ManualSleepStagesImport(BaseModel):
    connection_id: ConnectionId = None
    device_label: DeviceLabel
    stages: list[ManualSleepStageIn] = Field(min_length=1, description="The scored intervals, in any order.")
    sleep_start: AwareDatetime | None = Field(
        None, description="Start of the record (lights off). Default: the first stage's start."
    )
    sleep_end: AwareDatetime | None = Field(
        None, description="End of the record (lights on). Default: the last stage's end."
    )
    zone_offset: ZoneOffset = Field(
        None, description="The local offset of the night, `+HH:MM`. Default: the offset of `sleep_start`."
    )
    external_id: str | None = Field(
        None,
        max_length=100,
        description=(
            "The importer's own id for this night. Re-importing with the same id replaces the record. "
            "Default: derived from the device label and the record's start."
        ),
    )

    @model_validator(mode="after")
    def _window_is_ordered(self) -> "ManualSleepStagesImport":
        if self.sleep_start is not None and self.sleep_end is not None and self.sleep_end <= self.sleep_start:
            raise ValueError("sleep_end must be after sleep_start")
        return self


class ManualSleepImportResult(BaseModel):
    record_id: UUID = Field(description="The sleep record, as `/users/{user_id}/events/sleep` lists it.")
    connection_id: UUID
    data_source_id: UUID
    external_id: str
    start_time: datetime
    end_time: datetime
    stage_count: int
    replaced: bool = Field(description="True when a record with this external_id already existed and was replaced.")
    totals_scored: bool = Field(
        description=(
            "True when every stage label was one of OW's words, so the record's stage minutes, total sleep "
            "and efficiency are filled. False when any label was foreign (N1/N2/N3): the totals are then "
            "null rather than figures derived from a fold this store refuses to make."
        ),
    )


class ManualSampleIn(BaseModel):
    recorded_at: AwareDatetime
    value: Decimal | float | int


class ManualSeriesImport(BaseModel):
    connection_id: ConnectionId = None
    device_label: DeviceLabel
    series_type: SeriesType
    samples: list[ManualSampleIn] = Field(min_length=1, max_length=200_000)
    interval_seconds: int | None = Field(
        None,
        gt=0,
        description=(
            "Seconds of signal each value summarises, when the instrument states it (a 5-minute RMSSD is 300). "
            "Stored on every row and exposed as `interval_seconds`; the rows are then filed under the "
            "`manual.windows` feed rather than `manual.samples`."
        ),
    )
    zone_offset: ZoneOffset = Field(
        None, description="The local offset to stamp on every row, `+HH:MM`. Default: each sample's own offset."
    )


class ManualSeriesImportResult(BaseModel):
    connection_id: UUID
    data_source_id: UUID
    series_type: SeriesType
    route: str
    received: int
    inserted: int = Field(description="Rows that did not exist at that (source, type, second).")
    updated: int = Field(description="Rows that already existed; refreshed where the value differed, kept otherwise.")
