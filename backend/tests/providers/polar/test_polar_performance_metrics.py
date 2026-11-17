"""Polar's profile thresholds and per-exercise loads.

Payloads are the AccessLink spec's own examples (https://www.polar.com/accesslink-api/:
"Get user's physical info" and "List exercises"), trimmed to the fields read here.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import EventRecordAggregate, SeriesSample
from app.schemas.enums import ComputedBy, SampleRoute, SeriesType, get_series_type_id
from app.schemas.providers.polar import ExerciseJSON
from app.services.providers.polar.data_247 import Polar247Data
from app.services.providers.polar.workouts import PolarWorkouts
from tests.factories import ApiKeyFactory, UserConnectionFactory, UserFactory
from tests.providers.polar.test_polar_workouts import _polar_workouts
from tests.utils import api_key_headers

PHYSICAL_INFO: dict[str, Any] = {
    "weight": 70.5,
    "height": 175,
    "created": "2024-06-01T12:00:00Z",
    "modified": "2024-06-10T12:00:00Z",
    "birthday": "1990-01-01",
    "gender": "MALE",
    "maximum_heart_rate": 190,
    "resting_heart_rate": 60,
    "aerobic_threshold": 140,
    "anaerobic_threshold": 170,
    "vo2_max": 50,
    "weight_source": "SOURCE_USER",
    "training_background": "REGULAR",
    "typical_day": "MOSTLY_MOVING",
    "sleep_goal": "PT8H",
}

TRAINING_LOAD_PRO: dict[str, Any] = {
    "date": "2024-01-15",
    "cardio-load": 112.4,
    "muscle-load": 845.0,
    "perceived-load": 360.0,
    "cardio-load-interpretation": "MEDIUM",
    "muscle-load-interpretation": "HIGH",
    "perceived-load-interpretation": "MEDIUM",
    "user-rpe": "MODERATE",
}


@pytest.fixture
def polar_247() -> Polar247Data:
    return Polar247Data(provider_name="polar", api_base_url="https://www.polaraccesslink.com", oauth=MagicMock())


def _by_type(samples: list) -> dict[SeriesType, Decimal]:
    return {s.series_type: Decimal(str(s.value)) for s in samples}


class TestPhysicalInfo:
    def test_the_spec_example_gives_each_profile_figure_its_own_series(self, polar_247: Polar247Data) -> None:
        samples = polar_247.normalize_physical_info(PHYSICAL_INFO, uuid4())

        assert _by_type(samples) == {
            SeriesType.polar_aerobic_threshold: Decimal(140),
            SeriesType.polar_anaerobic_threshold: Decimal(170),
            SeriesType.polar_profile_vo2_max: Decimal(50),
            SeriesType.polar_profile_max_heart_rate: Decimal(190),
            SeriesType.polar_profile_resting_heart_rate: Decimal(60),
        }
        assert {s.recorded_at for s in samples} == {datetime(2024, 6, 10, 12, tzinfo=timezone.utc)}
        assert {s.route for s in samples} == {SampleRoute.POLAR_PHYSICAL_INFO}

    def test_a_profile_setting_never_lands_in_a_measured_series(self, polar_247: Polar247Data) -> None:
        series = {s.series_type for s in polar_247.normalize_physical_info(PHYSICAL_INFO, uuid4())}

        assert not series & {SeriesType.resting_heart_rate, SeriesType.vo2_max, SeriesType.heart_rate}

    @pytest.mark.parametrize("missing", [None, 0])
    def test_a_missing_or_zero_figure_writes_no_row(self, polar_247: Polar247Data, missing: int | None) -> None:
        samples = polar_247.normalize_physical_info({**PHYSICAL_INFO, "aerobic_threshold": missing}, uuid4())

        assert SeriesType.polar_aerobic_threshold not in _by_type(samples)
        assert SeriesType.polar_anaerobic_threshold in _by_type(samples)

    def test_without_modified_the_creation_time_dates_it(self, polar_247: Polar247Data) -> None:
        samples = polar_247.normalize_physical_info({**PHYSICAL_INFO, "modified": None}, uuid4())

        assert {s.recorded_at for s in samples} == {datetime(2024, 6, 1, 12, tzinfo=timezone.utc)}

    @pytest.mark.parametrize("stamp", [None, "not a date"])
    def test_an_undated_profile_gives_nothing(self, polar_247: Polar247Data, stamp: str | None) -> None:
        raw = {**PHYSICAL_INFO, "modified": stamp, "created": stamp}

        assert polar_247.normalize_physical_info(raw, uuid4()) == []

    def test_no_profile_gives_nothing(self, polar_247: Polar247Data) -> None:
        assert polar_247.normalize_physical_info(None, uuid4()) == []

    def test_saved_profile_rows_are_samples_and_a_resync_keeps_one_each(self, db: Session) -> None:
        user = UserFactory()
        UserConnectionFactory(user=user, provider="polar")
        polar_247 = Polar247Data(provider_name="polar", api_base_url="https://x", oauth=MagicMock())

        for _ in range(2):
            polar_247._save_timeseries(db, polar_247.normalize_physical_info(PHYSICAL_INFO, user.id))
            db.commit()

        rows = (
            db.query(SeriesSample)
            .filter(SeriesSample.series_type_definition_id == get_series_type_id(SeriesType.polar_aerobic_threshold))
            .all()
        )
        assert [row.value for row in rows] == [Decimal(140)]


class TestExerciseSessionValues:
    def _exercise(self, base: dict, **extra: Any) -> ExerciseJSON:
        return ExerciseJSON(**{**base, **extra})

    def test_every_figure_is_filed_against_its_workout(self, sample_polar_exercise: dict) -> None:
        exercise = self._exercise(
            sample_polar_exercise, **{"running-index": 51, "training_load_pro": TRAINING_LOAD_PRO}
        )
        record_id = uuid4()
        start = datetime(2024, 1, 15, 7, tzinfo=timezone.utc)

        values = PolarWorkouts._session_values(exercise, uuid4(), record_id, start, "+01:00")

        assert _by_type(values) == {
            SeriesType.polar_running_index: Decimal(51),
            SeriesType.polar_training_load: Decimal("150.0"),
            SeriesType.polar_cardio_load: Decimal("112.4"),
            SeriesType.polar_muscle_load: Decimal("845.0"),
            SeriesType.polar_perceived_load: Decimal("360.0"),
        }
        assert {v.session.record_id for v in values if v.session} == {record_id}
        assert all(v.session is not None for v in values)
        assert {v.recorded_at for v in values} == {start}

    def test_a_figure_polar_left_out_writes_no_row(self, sample_polar_exercise: dict) -> None:
        """No power sensor, no muscle load; a cycling session, no running index. Neither is zero."""
        pro = {k: v for k, v in TRAINING_LOAD_PRO.items() if not k.startswith("muscle-load")}
        exercise = self._exercise(sample_polar_exercise, training_load_pro=pro, training_load=None)

        values = PolarWorkouts._session_values(exercise, uuid4(), uuid4(), datetime.now(timezone.utc), None)

        assert set(_by_type(values)) == {SeriesType.polar_cardio_load, SeriesType.polar_perceived_load}

    @patch("app.services.providers.templates.base_workouts.make_authenticated_request")
    def test_a_synced_exercise_stores_its_figures_once_per_workout(
        self, mock_request: MagicMock, db: Session, sample_polar_exercise: dict
    ) -> None:
        user = UserFactory()
        UserConnectionFactory(user=user, provider="polar")
        workouts = _polar_workouts()
        mock_request.return_value = [
            {**sample_polar_exercise, "running-index": 51, "training_load_pro": TRAINING_LOAD_PRO}
        ]

        # Flow re-lists the last 30 days on every pull.
        workouts.load_data(db, user.id)
        workouts.load_data(db, user.id)

        rows = {
            row.series_type_definition_id: row
            for row in db.query(EventRecordAggregate).filter(EventRecordAggregate.computed_by == ComputedBy.PROVIDER)
        }
        load = rows[get_series_type_id(SeriesType.polar_cardio_load)]
        running_index = rows[get_series_type_id(SeriesType.polar_running_index)]
        assert load.sum == Decimal("112.4")
        assert running_index.avg == Decimal(51)
        assert load.record_id == running_index.record_id
        per_series = db.query(EventRecordAggregate).filter(
            EventRecordAggregate.series_type_definition_id == get_series_type_id(SeriesType.polar_cardio_load)
        )
        assert per_series.count() == 1


@patch("app.services.providers.templates.base_workouts.make_authenticated_request")
def test_the_workout_listing_returns_polar_s_figures(
    mock_request: MagicMock, db: Session, client: TestClient, sample_polar_exercise: dict
) -> None:
    user = UserFactory()
    UserConnectionFactory(user=user, provider="polar")
    start = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=2)
    exercise = {
        **sample_polar_exercise,
        "start_time": start.strftime("%Y-%m-%dT%H:%M:%S"),
        "start_time_utc_offset": 0,
        "running-index": 51,
        "training_load_pro": {k: v for k, v in TRAINING_LOAD_PRO.items() if not k.startswith("muscle-load")},
    }
    mock_request.return_value = [exercise]
    _polar_workouts().load_data(db, user.id)

    response = client.get(
        f"/api/v1/users/{user.id}/events/workouts",
        headers=api_key_headers(ApiKeyFactory().plain_key),
        params={
            "start_date": (start - timedelta(days=1)).isoformat(),
            "end_date": datetime.now(timezone.utc).isoformat(),
        },
    )

    assert response.status_code == 200
    (workout,) = response.json()["data"]
    assert workout["provider_metrics"] == {
        "polar_cardio_load": {"value": 112.4, "unit": "score"},
        "polar_perceived_load": {"value": 360.0, "unit": "score"},
        "polar_running_index": {"value": 51.0, "unit": "score"},
        "polar_training_load": {"value": 150.0, "unit": "score"},
    }


class TestKeySpellings:
    """The spec writes kebab-case; /v3/exercises has sent snake_case for other keys (DECISIONS.md)."""

    def test_snake_case_keys_read_like_the_spec_s(self, sample_polar_exercise: dict) -> None:
        snake = {key.replace("-", "_"): value for key, value in TRAINING_LOAD_PRO.items()}
        kebab_exercise = ExerciseJSON(
            **{**sample_polar_exercise, "running-index": 51, "training-load-pro": TRAINING_LOAD_PRO}
        )
        snake_exercise = ExerciseJSON(**{**sample_polar_exercise, "running_index": 51, "training_load_pro": snake})

        def figures(exercise: ExerciseJSON) -> dict[SeriesType, Decimal]:
            return _by_type(PolarWorkouts._session_values(exercise, uuid4(), uuid4(), datetime.now(timezone.utc), None))

        assert figures(snake_exercise) == figures(kebab_exercise)
        assert figures(snake_exercise)[SeriesType.polar_running_index] == Decimal(51)
        assert figures(snake_exercise)[SeriesType.polar_muscle_load] == Decimal("845.0")

    def test_a_load_object_that_parses_to_nothing_alerts_with_its_keys(self, sample_polar_exercise: dict) -> None:
        exercise = ExerciseJSON(**{**sample_polar_exercise, "training_load_pro": {"cardioLoad": 112.4}})

        with patch("app.services.providers.polar.workouts.logger") as logger:
            values = PolarWorkouts._session_values(exercise, uuid4(), uuid4(), datetime.now(timezone.utc), None)

        assert SeriesType.polar_cardio_load not in _by_type(values)
        assert logger.error.call_args.kwargs["alert"] is True
        assert logger.error.call_args.kwargs["unread_keys"] == ["cardioLoad"]

    def test_an_empty_load_object_is_no_alert(self, sample_polar_exercise: dict) -> None:
        """Polar sends the object with only labels when a session has no load: that is not a mismatch."""
        pro = {"date": "2024-01-15", "cardio-load-interpretation": "UNKNOWN", "user-rpe": "UNKNOWN"}
        exercise = ExerciseJSON(**{**sample_polar_exercise, "training_load_pro": pro})

        with patch("app.services.providers.polar.workouts.logger") as logger:
            PolarWorkouts._session_values(exercise, uuid4(), uuid4(), datetime.now(timezone.utc), None)

        logger.error.assert_not_called()
