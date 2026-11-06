"""Heart-rate samples by the sensor each one says it came from.

See app/repositories/heart_rate_sensor_repository.py for why: a strap paired to an
Apple Watch is filed under the Watch, and only the sample's own metadata says chest.
"""

from collections import Counter
from datetime import datetime, timedelta
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select

from app.database import DbSession
from app.models import DataSource
from app.repositories.heart_rate_sensor_repository import sensor_runs
from app.schemas.enums import SeriesType, get_series_type_id
from app.schemas.responses.activity import HeartRateSensorRun, HeartRateSensorSource, HeartRateSensorsResponse
from app.schemas.responses.activity.heart_rate_sensors import SENSOR_LOCATIONS

# A workout or a night is hours; a week bounds a request that reads every sample in it.
MAX_WINDOW = timedelta(days=7)
NOT_STATED = "not_stated"


def _location(raw: str | None) -> str | None:
    if raw is None:
        return None
    cleaned = raw.strip()
    return SENSOR_LOCATIONS.get(cleaned, f"unrecognised:{cleaned}")


def heart_rate_sensors(
    db: DbSession,
    user_id: UUID,
    start_time: datetime,
    end_time: datetime,
    data_source_id: UUID | None = None,
) -> HeartRateSensorsResponse:
    if end_time <= start_time:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="end_time must be after start_time")
    if end_time - start_time > MAX_WINDOW:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"The window is at most {MAX_WINDOW.days} days; ask for a workout or a night at a time.",
        )
    runs = sensor_runs(db, user_id, get_series_type_id(SeriesType.heart_rate), start_time, end_time, data_source_id)

    by_source: dict[UUID, Counter[str]] = {}
    for run in runs:
        counts = by_source.setdefault(run.data_source_id, Counter())
        counts[_location(run.location_raw) or NOT_STATED] += run.samples

    sources = (
        {ds.id: ds for ds in db.scalars(select(DataSource).where(DataSource.id.in_(by_source.keys()))).all()}
        if by_source
        else {}
    )

    return HeartRateSensorsResponse(
        start_time=start_time,
        end_time=end_time,
        sources=[
            HeartRateSensorSource(
                data_source_id=ds_id,
                provider=getattr(sources[ds_id].provider, "value", sources[ds_id].provider),
                source=sources[ds_id].source,
                device_model=sources[ds_id].device_model,
                device_id=sources[ds_id].device_id,
                samples=sum(counts.values()),
                by_location=dict(counts.most_common()),
            )
            for ds_id, counts in by_source.items()
            if ds_id in sources
        ],
        runs=[
            HeartRateSensorRun(
                data_source_id=run.data_source_id,
                sensor_location=_location(run.location_raw),
                sensor_location_raw=run.location_raw,
                first_at=run.first_at,
                last_at=run.last_at,
                samples=run.samples,
                sedentary=run.sedentary,
                active=run.active,
                context_not_stated=run.context_not_stated,
            )
            for run in runs
        ],
    )
