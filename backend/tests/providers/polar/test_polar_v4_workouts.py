"""Tests for storing Polar workouts from v4 training sessions.

v3 lists only the last 30 days, and only exercises that reached Flow after the account was
registered with this app. v4 lists sessions by date, so these tests pin down that a session
only v4 knows becomes a workout, that one v3 already stored is not stored twice in either
order, and that a session that cannot be placed in time is skipped rather than guessed.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

from app.models import DataSource, EventRecord, User
from app.schemas.enums import ProviderName, WorkoutType
from app.schemas.providers.polar.v4 import TrainingSessionJSON
from app.services.providers.polar.strategy import PolarStrategy
from app.services.providers.polar.v4_data import PolarV4Data, polar_v4_data
from app.services.providers.polar.workouts import PolarWorkouts
from tests.factories import DataSourceFactory, EventRecordFactory, UserConnectionFactory, UserFactory

# 08:59:30 local in US Eastern daylight time, so 12:59:30 UTC.
V4_SESSION = {
    "identifier": {"id": "abc123"},
    "startTime": "2026-10-13T08:59:30",
    "stopTime": "2026-10-13T10:59:30",
    "durationMillis": 7_200_000,
    "timezoneOffsetMinutes": -240,
    "deviceId": "D1",
    "product": {"modelName": "Grit X2 Pro"},
    "sport": "RUNNING",
    "distanceMeters": 21097.5,
    "calories": 1450,
    "hrAvg": 151,
    "hrMax": 178,
}

V3_EXERCISE = {
    "id": "v3-1",
    "upload_time": "2026-10-13T15:00:00Z",
    "polar_user": "https://www.polaraccesslink.com/v3/users/1",
    "device": "Polar Grit X2 Pro",
    "device_id": "D1",
    "start_time": "2026-10-13T08:59:30",
    "start_time_utc_offset": -240,
    "duration": "PT2H",
    "sport": "RUNNING",
    "has_route": False,
    "detailed_sport_info": "RUNNING",
}

START_UTC = datetime(2026, 10, 13, 12, 59, 30, tzinfo=timezone.utc)
END_UTC = datetime(2026, 10, 13, 14, 59, 30, tzinfo=timezone.utc)
SYNC_WINDOW = {"start_date": "2026-10-10T00:00:00+00:00", "end_date": "2026-10-15T00:00:00+00:00"}


@pytest.fixture
def workouts() -> PolarWorkouts:
    return PolarStrategy().workouts


@pytest.fixture
def user(db: Session) -> User:
    user = UserFactory()
    UserConnectionFactory(user=user, provider="polar")
    UserConnectionFactory(user=user, provider=ProviderName.POLAR_V4.value)
    db.flush()
    return user


def _polar_workouts(db: Session, user: User) -> list[EventRecord]:
    return (
        db.query(EventRecord)
        .join(DataSource, EventRecord.data_source_id == DataSource.id)
        .filter(DataSource.user_id == user.id, DataSource.provider == ProviderName.POLAR)
        .order_by(EventRecord.start_datetime)
        .all()
    )


def _load(workouts: PolarWorkouts, db: Session, user: User, v3: list[dict], v4: list[dict]) -> int:
    sessions = [TrainingSessionJSON(**session) for session in v4]
    with (
        patch.object(workouts, "_make_api_request", return_value=v3),
        patch.object(polar_v4_data, "list_training_sessions", return_value=sessions),
        patch.object(polar_v4_data, "rr_rows_for_session", return_value=None),
    ):
        return workouts.load_data(db, user.id, **SYNC_WINDOW)


class TestSessionsOnlyV4Lists:
    def test_a_session_v3_never_listed_becomes_a_workout_at_true_utc(
        self, db: Session, user: User, workouts: PolarWorkouts
    ) -> None:
        assert _load(workouts, db, user, v3=[], v4=[V4_SESSION]) == 1

        [record] = _polar_workouts(db, user)
        assert record.start_datetime == START_UTC
        assert record.end_datetime == END_UTC
        assert record.zone_offset == "-04:00"
        assert record.external_id == "v4:abc123"
        assert record.type == WorkoutType.RUNNING.value
        assert record.source_name == "Polar Grit X2 Pro"
        detail = record.workout_detail
        assert detail.heart_rate_avg == Decimal("151")
        assert detail.heart_rate_max == 178
        assert detail.energy_burned == Decimal("1450")
        assert detail.distance == Decimal("21097.5")

    def test_a_second_pull_does_not_add_it_again(self, db: Session, user: User, workouts: PolarWorkouts) -> None:
        _load(workouts, db, user, v3=[], v4=[V4_SESSION])

        assert _load(workouts, db, user, v3=[], v4=[V4_SESSION]) == 0
        assert len(_polar_workouts(db, user)) == 1

    def test_a_session_v3_already_stored_is_left_alone(self, db: Session, user: User, workouts: PolarWorkouts) -> None:
        _load(workouts, db, user, v3=[V3_EXERCISE], v4=[V4_SESSION])

        [record] = _polar_workouts(db, user)
        assert record.external_id == "v3-1"

    def test_v3_does_not_add_a_copy_of_a_session_v4_stored_first(
        self, db: Session, user: User, workouts: PolarWorkouts
    ) -> None:
        _load(workouts, db, user, v3=[], v4=[V4_SESSION])

        # v3's bounds differ from v4's by a second, so the unique key would not catch it.
        exercise = {**V3_EXERCISE, "start_time": "2026-10-13T08:59:31"}
        _load(workouts, db, user, v3=[exercise], v4=[])

        [record] = _polar_workouts(db, user)
        assert record.external_id == "v4:abc123"

    def test_another_providers_workout_at_the_same_time_does_not_block_it(
        self, db: Session, user: User, workouts: PolarWorkouts
    ) -> None:
        garmin = DataSourceFactory(user=user, provider=ProviderName.GARMIN, source="garmin")
        EventRecordFactory(data_source=garmin, start_datetime=START_UTC, end_datetime=END_UTC)
        db.flush()

        assert _load(workouts, db, user, v3=[], v4=[V4_SESSION]) == 1

    def test_a_session_without_a_utc_offset_is_skipped_not_guessed(
        self, db: Session, user: User, workouts: PolarWorkouts
    ) -> None:
        session = {key: value for key, value in V4_SESSION.items() if key != "timezoneOffsetMinutes"}

        assert _load(workouts, db, user, v3=[], v4=[session]) == 0
        assert _polar_workouts(db, user) == []

    def test_sport_as_an_object_is_read_by_name(self, db: Session, user: User, workouts: PolarWorkouts) -> None:
        _load(workouts, db, user, v3=[], v4=[{**V4_SESSION, "sport": {"id": "1", "name": "running"}}])

        [record] = _polar_workouts(db, user)
        assert record.type == WorkoutType.RUNNING.value

    def test_v4_beats_are_stored_with_the_workout(self, db: Session, user: User, workouts: PolarWorkouts) -> None:
        with (
            patch.object(workouts, "_make_api_request", return_value=[]),
            patch.object(polar_v4_data, "list_training_sessions", return_value=[TrainingSessionJSON(**V4_SESSION)]),
            patch.object(polar_v4_data, "rr_rows_for_session", return_value=[(1000, False), (900, False)]) as rr,
            patch.object(workouts, "_save_rr_samples", return_value=2) as save,
        ):
            workouts.load_data(db, user.id, **SYNC_WINDOW)

        assert rr.call_args.args[2] == datetime(2026, 10, 13, 8, 59, 30)
        [beats] = save.call_args.args[1:]
        assert [beat.value for beat in beats] == [1000, 900]

    def test_nothing_is_asked_of_v4_without_a_v4_connection(self, db: Session, workouts: PolarWorkouts) -> None:
        user = UserFactory()
        UserConnectionFactory(user=user, provider="polar")
        db.flush()

        with (
            patch.object(workouts, "_make_api_request", return_value=[]),
            patch.object(polar_v4_data, "list_training_sessions") as listing,
        ):
            assert workouts.load_data(db, user.id, **SYNC_WINDOW) == 0
        listing.assert_not_called()

    def test_the_sync_window_is_read_with_a_day_of_margin(self) -> None:
        assert PolarWorkouts._sync_days(SYNC_WINDOW["start_date"], SYNC_WINDOW["end_date"]) == (
            date(2026, 10, 9),
            date(2026, 10, 16),
        )


class TestListTrainingSessions:
    def test_a_long_range_is_read_in_chunks_without_features(self) -> None:
        v4 = PolarV4Data()
        with patch.object(v4, "_get", return_value={"trainingSessions": [V4_SESSION]}) as get:
            sessions = v4.list_training_sessions(None, None, date(2026, 7, 1), date(2026, 9, 28))  # type: ignore[arg-type]

        ranges = [(call.args[3]["from"], call.args[3]["to"]) for call in get.call_args_list]
        assert ranges == [
            ("2026-07-01", "2026-07-29"),
            ("2026-07-29", "2026-08-26"),
            ("2026-08-26", "2026-09-23"),
            ("2026-09-23", "2026-09-29"),
        ]
        assert all("features" not in call.args[3] for call in get.call_args_list)
        assert len(sessions) == 4
