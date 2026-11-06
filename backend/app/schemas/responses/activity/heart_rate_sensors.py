"""Which sensor each heart-rate sample came from, read from HealthKit's per-sample metadata."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

# HKHeartRateSensorLocation raw values (HealthKit). Anything else is reported verbatim.
SENSOR_LOCATIONS: dict[str, str] = {
    "0": "other",
    "1": "chest",
    "2": "wrist",
    "3": "finger",
    "4": "hand",
    "5": "ear_lobe",
    "6": "foot",
}


class HeartRateSensorRun(BaseModel):
    """A stretch of one source's heart-rate samples that share a stated sensor location."""

    data_source_id: UUID
    sensor_location: str | None = Field(
        None,
        description=(
            "Where the sample says it was measured (HKMetadataKeyHeartRateSensorLocation): chest, wrist, finger, "
            "hand, ear_lobe, foot or other. Null when the sample did not say, which is not the same as wrist."
        ),
        examples=["chest"],
    )
    sensor_location_raw: str | None = Field(None, description="The stored value, verbatim.")
    first_at: datetime
    last_at: datetime
    samples: int
    sedentary: int = Field(..., description="Samples whose motion context says sedentary.")
    active: int = Field(..., description="Samples whose motion context says active.")
    context_not_stated: int = Field(..., description="Samples with no motion context, or 'not set'.")


class HeartRateSensorSource(BaseModel):
    """One data source's heart-rate samples in the window, counted by stated sensor location."""

    data_source_id: UUID
    provider: str
    source: str | None = None
    device_model: str | None = None
    device_id: UUID | None = None
    samples: int
    by_location: dict[str, int] = Field(
        ...,
        description="Samples per stated location; `not_stated` counts samples that carried no location.",
        examples=[{"wrist": 512, "chest": 1840, "not_stated": 12}],
    )


class HeartRateSensorsResponse(BaseModel):
    """Heart-rate samples in a window, by source and by the sensor each one says it came from.

    Built for the case no data source can show: a chest strap paired to an Apple Watch is written
    under the Watch's own source, so only the per-sample sensor location tells strap from wrist.
    Only what the platform stored is reported: samples synced before metadata was kept, or through
    the Apple Health XML import (which drops it), count as `not_stated`.
    """

    start_time: datetime
    end_time: datetime
    sources: list[HeartRateSensorSource]
    runs: list[HeartRateSensorRun] = Field(
        ..., description="In time order. Consecutive runs of one source with different locations mark a switch."
    )
