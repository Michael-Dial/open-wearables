"""A Fitbit activity is dated onto the tracker its log names (``source.id``)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from sqlalchemy.orm import Session

from app.models import DataSourceObservation
from app.services.providers.fitbit.workouts import FitbitWorkouts
from tests.factories import UserFactory


def test_load_data_dates_the_tracker_by_its_activities(db: Session) -> None:
    user = UserFactory()
    workouts = FitbitWorkouts(
        workout_repo=MagicMock(),
        connection_repo=MagicMock(),
        provider_name="fitbit",
        api_base_url="https://api.fitbit.com",
        oauth=MagicMock(),
    )
    activity = {
        "logId": 12345678,
        "activityName": "Run",
        "activityTypeId": 90009,
        "startTime": "2024-01-15T07:30:00.000+00:00",
        "duration": 3600000,
        "source": {"id": "310492015", "name": "Charge 6", "type": "tracker"},
    }
    workouts.get_workouts = MagicMock(return_value=[activity])  # type: ignore[method-assign]

    workouts.load_data(db, user.id)

    [obs] = db.query(DataSourceObservation).filter(DataSourceObservation.user_id == user.id).all()
    assert obs.device_local_identifier == "310492015"
    assert obs.source_name == "Charge 6"
    assert obs.first_recorded_at == datetime(2024, 1, 15, 7, 30, tzinfo=timezone.utc)
    assert obs.last_recorded_at == datetime(2024, 1, 15, 8, 30, tzinfo=timezone.utc)
