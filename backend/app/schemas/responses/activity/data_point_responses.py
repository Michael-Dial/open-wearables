from datetime import date, datetime
from typing import TypedDict
from uuid import UUID

from pydantic import BaseModel

from app.schemas.enums import ComputedBy, SeriesType
from app.schemas.utils import SourceMetadata
from app.utils.dates import ZoneOffset


class SeriesStats(BaseModel):
    """Every statistic held for a period. The sample's value is the one its series is read by."""

    avg: float | None = None
    min: float | None = None
    max: float | None = None
    sum: float | None = None
    last: float | None = None
    count: int


class TimeSeriesSample(BaseModel):
    timestamp: datetime
    zone_offset: ZoneOffset = None
    type: SeriesType
    value: float | int
    unit: str
    source: SourceMetadata | None = None
    # True = daily total. False/None = not a daily total (summable sample); None is a
    # legacy row and is treated as False by the aggregation.
    is_daily_total: bool | None = None
    # Seconds of signal the value summarises, when the provider states it (Oura's sleep
    # `hrv` / `heart_rate` arrays are 300 s windows). None = not stated, never a guess:
    # an RMSSD over five minutes and one over a minute are different measurements, and a
    # consumer re-windowing a reference recording onto this sample has to know which.
    # Only raw samples carry it; an aggregated bucket's span is the requested resolution.
    interval_seconds: int | None = None
    # The feed that wrote the sample (e.g. "garmin.activity_details", "garmin.dailies")
    # and its kind: workout | intraday | window | summary. One data source can hold
    # several feeds of one series - a watch's workout trace and its all-day monitoring -
    # and these are what tell them apart. None on a row written before feeds were
    # recorded. Raw samples only.
    route: str | None = None
    route_kind: str | None = None
    # Beat-to-beat rows only (Apple heartbeat series in pulse_to_pulse_interval): the
    # HealthKit series the beat belongs to, and HealthKit's flag that beats may be missing
    # just before it - in which case `value` spans the gap and is not a beat interval.
    # None on every other sample. Raw samples only.
    beat_series_id: str | None = None
    preceded_by_gap: bool | None = None
    # Which estimate a fitness value is, verbatim as its reader recorded it under
    # provider_metadata["estimate_method"] (Google: the data type, "run-vo2-max" or
    # "vo2-max"). None = not stated, never a default: one device can publish two VO2max
    # algorithms into one series, and a comparison that pools them compares neither.
    # Raw samples only.
    estimate_method: str | None = None
    # Width of a sample reported over a window (a 15-minute epoch is 900); None for an instant.
    window_seconds: int | None = None
    # Day, week and month resolutions: the period's end, who computed it, and all its statistics.
    period_end: datetime | None = None
    computed_by: ComputedBy | None = None
    stats: SeriesStats | None = None


class ActivityAggregateResult(TypedDict):
    """Result from daily activity aggregation query."""

    activity_date: date
    provider: str | None
    source: str | None
    device_model: str | None
    # The account the data came through. Part of the data source's identity, so two
    # accounts with one provider are two rows rather than one row summing both.
    user_connection_id: UUID | None
    device_type: str | None
    steps_sum: int | None
    active_energy_sum: float | None
    basal_energy_sum: float | None
    total_energy_sum: float | None
    hr_avg: int | None
    hr_max: int | None
    hr_min: int | None
    distance_sum: float | None
    flights_climbed_sum: int | None
    active_time_minutes: int | None  # Provider-reported daily active time; None when not reported


class ActiveMinutesResult(TypedDict):
    """Result from daily active/sedentary minutes query."""

    activity_date: date
    source: str | None
    device_model: str | None
    user_connection_id: UUID | None
    active_minutes: int
    tracked_minutes: int
    sedentary_minutes: int


class IntensityMinutesResult(TypedDict):
    """Result from daily intensity minutes query."""

    activity_date: date
    source: str | None
    device_model: str | None
    user_connection_id: UUID | None
    light_minutes: int
    moderate_minutes: int
    vigorous_minutes: int
