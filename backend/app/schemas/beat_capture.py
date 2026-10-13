"""Beat-to-beat capture: what a participant's phone collects, and windows asked of it.

Researcher-facing schemas carry the full row; the SDK-facing ones carry only what the
phone needs to decide what to read from HealthKit.
"""

from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.config import settings


class BeatCaptureMode(StrEnum):
    """Which heartbeat series the phone uploads without being asked.

    Requested windows are fulfilled in every mode, ``off`` included.
    """

    OFF = "off"
    SLEEP = "sleep"
    ALL_DAY = "all_day"


class BeatCaptureWindowStatus(StrEnum):
    PENDING = "pending"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"


class BeatCaptureSettingRead(BaseModel):
    user_id: UUID
    mode: BeatCaptureMode
    lookback_days: int = Field(description="How far back the automatic modes reach on the phone, in days.")
    sleep_padding_minutes: int = Field(description="Minutes added either side of each sleep session in sleep mode.")
    is_default: bool = Field(description="True when nothing is stored for this user and the instance default applies.")
    updated_at: datetime | None = None


class BeatCaptureSettingUpdate(BaseModel):
    """Fields left out keep their current value (or the instance default)."""

    mode: BeatCaptureMode | None = None
    lookback_days: int | None = Field(None, ge=1, le=30)
    sleep_padding_minutes: int | None = Field(None, ge=0, le=240)


class BeatCaptureWindowCreate(BaseModel):
    start_at: datetime = Field(description="Window start (timezone-aware ISO 8601).")
    end_at: datetime = Field(description="Window end, exclusive (timezone-aware ISO 8601).")
    note: str | None = Field(None, max_length=255, description="Free text, e.g. 'PSG night 3'.")

    @model_validator(mode="after")
    def _check_span(self) -> "BeatCaptureWindowCreate":
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise ValueError("start_at and end_at must carry a timezone offset")
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        if self.end_at - self.start_at > timedelta(days=settings.beat_capture_max_window_days):
            raise ValueError(
                f"a window may span at most {settings.beat_capture_max_window_days} days; request several instead"
            )
        return self


class BeatCaptureWindowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    start_at: datetime
    end_at: datetime
    status: BeatCaptureWindowStatus
    note: str | None = None
    created_at: datetime
    fulfilled_at: datetime | None = None
    beats_reported: int | None = Field(None, description="Beats the phone uploaded for this window.")
    series_reported: int | None = Field(None, description="Heartbeat series the phone found in this window.")


class SDKBeatCaptureWindow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    start_at: datetime
    end_at: datetime


class SDKBeatCaptureConfig(BaseModel):
    """What the iOS SDK reads before each sync."""

    mode: BeatCaptureMode
    lookback_days: int
    sleep_padding_minutes: int
    windows: list[SDKBeatCaptureWindow] = Field(description="Pending windows to upload, whatever the mode.")


class SDKBeatCaptureFulfilment(BaseModel):
    """The phone's report that it has uploaded everything a window covers."""

    beats: int = Field(ge=0)
    series: int = Field(ge=0)
