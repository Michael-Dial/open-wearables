"""Which sensor each heart-rate sample came from, as HealthKit recorded it.

When a chest strap is paired to an Apple Watch, the Watch writes the strap's heart
rate under its own source; only the per-sample metadata says where on the body it was
measured (``HKMetadataKeyHeartRateSensorLocation``). The SDK stores that metadata
verbatim in ``series_sample.provider_metadata``; this reads it back as runs: stretches
of one source's samples that share a stated sensor location, in time order, so a
switch from wrist to chest inside a workout is a boundary between two runs.

One statement for the whole window (gaps and islands over a window function), so the
cost does not grow with the number of sources.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import text

from app.database import DbSession

LOCATION_KEY = "HKMetadataKeyHeartRateSensorLocation"
MOTION_KEY = "HKMetadataKeyHeartRateMotionContext"

_RUNS = text(
    f"""
    WITH s AS (
        SELECT ss.data_source_id,
               ss.recorded_at,
               ss.provider_metadata ->> '{LOCATION_KEY}' AS loc,
               ss.provider_metadata ->> '{MOTION_KEY}' AS ctx
        FROM series_sample ss
        JOIN data_source ds ON ds.id = ss.data_source_id
        WHERE ds.user_id = :user_id
          AND ss.series_type_definition_id = :series_type_id
          AND ss.recorded_at >= :start_time
          AND ss.recorded_at < :end_time
          AND (CAST(:data_source_id AS uuid) IS NULL OR ss.data_source_id = CAST(:data_source_id AS uuid))
    ),
    r AS (
        SELECT s.*,
               row_number() OVER (PARTITION BY data_source_id ORDER BY recorded_at)
             - row_number() OVER (PARTITION BY data_source_id, loc ORDER BY recorded_at) AS grp
        FROM s
    )
    SELECT data_source_id,
           loc,
           min(recorded_at) AS first_at,
           max(recorded_at) AS last_at,
           count(*) AS samples,
           count(*) FILTER (WHERE ctx = '1') AS sedentary,
           count(*) FILTER (WHERE ctx = '2') AS active,
           count(*) FILTER (WHERE ctx IS NULL OR ctx NOT IN ('1', '2')) AS context_not_stated
    FROM r
    GROUP BY data_source_id, loc, grp
    ORDER BY min(recorded_at), data_source_id
    """
)


@dataclass(frozen=True)
class SensorRun:
    data_source_id: UUID
    location_raw: str | None
    first_at: datetime
    last_at: datetime
    samples: int
    sedentary: int
    active: int
    context_not_stated: int


def sensor_runs(
    db_session: DbSession,
    user_id: UUID,
    series_type_id: int,
    start_time: datetime,
    end_time: datetime,
    data_source_id: UUID | None = None,
) -> list[SensorRun]:
    rows = db_session.execute(
        _RUNS,
        {
            "user_id": user_id,
            "series_type_id": series_type_id,
            "start_time": start_time,
            "end_time": end_time,
            "data_source_id": str(data_source_id) if data_source_id else None,
        },
    ).all()
    return [
        SensorRun(
            data_source_id=row.data_source_id,
            location_raw=row.loc,
            first_at=row.first_at,
            last_at=row.last_at,
            samples=row.samples,
            sedentary=row.sedentary,
            active=row.active,
            context_not_stated=row.context_not_stated,
        )
        for row in rows
    ]
