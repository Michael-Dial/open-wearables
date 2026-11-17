"""Garmin activity FIT files: their Training Effect, load and thresholds are filed against the workout.

Runs the webhook batch path against a real database: activityDetails stores the workout, then
activityFiles delivers the FIT file (download patched to a synthetic one) for the same activity.
"""

from typing import Any
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import EventRecord, EventRecordAggregate
from app.repositories.user_connection_repository import UserConnectionRepository
from app.schemas.enums import ComputedBy, SeriesType, get_series_type_id
from app.services.providers.garmin.data_247 import Garmin247Data
from app.services.providers.garmin.oauth import GarminOAuth
from app.utils.connection_context import active_connection
from tests.factories import ApiKeyFactory, UserConnectionFactory, UserFactory
from tests.fixtures.fit_builder import make_performance_fit
from tests.utils import api_key_headers

ACTIVITY_ID = 24621527099
RUN_START = 1791270000


def _activity_details() -> dict[str, Any]:
    return {
        "summaryId": f"{ACTIVITY_ID}-detail",
        "activityId": ACTIVITY_ID,
        "summary": {
            "activityId": ACTIVITY_ID,
            "activityType": "RUNNING",
            "deviceName": "forerunner 970",
            "startTimeInSeconds": RUN_START,
            "startTimeOffsetInSeconds": -14_400,
            "durationInSeconds": 1800,
        },
        "samples": [],
    }


def _activity_file() -> dict[str, Any]:
    return {
        "summaryId": f"{ACTIVITY_ID}-file",
        "activityId": ACTIVITY_ID,
        "fileType": "FIT",
        "callbackURL": "https://apis.garmin.com/wellness-api/rest/activityFile?id=1",
    }


@pytest.fixture
def garmin_247() -> Garmin247Data:
    oauth = GarminOAuth(
        user_repo=MagicMock(),
        connection_repo=UserConnectionRepository(),
        provider_name="garmin",
        api_base_url="https://apis.garmin.com",
    )
    return Garmin247Data(provider_name="garmin", api_base_url="https://apis.garmin.com", oauth=oauth)


def _push(garmin_247: Garmin247Data, db: Session, connection_id: UUID, user_id: UUID, kind: str, item: dict) -> None:
    with active_connection(connection_id):
        garmin_247.process_items_batch(db, user_id, kind, [item])
    db.commit()


def _sync(garmin_247: Garmin247Data, db: Session, fit: bytes) -> tuple[UUID, UUID]:
    user = UserFactory()
    conn = UserConnectionFactory(user=user, provider="garmin")
    _push(garmin_247, db, conn.id, user.id, "activityDetails", _activity_details())
    with patch("app.services.providers.garmin.data_247.download_binary_content", return_value=fit):
        _push(garmin_247, db, conn.id, user.id, "activityFiles", _activity_file())
    record = db.query(EventRecord).filter(EventRecord.external_id == str(ACTIVITY_ID)).one()
    return user.id, record.id


def _figures(db: Session, record_id: UUID) -> dict[SeriesType, float]:
    wanted = {
        get_series_type_id(t): t
        for t in (
            SeriesType.aerobic_training_effect,
            SeriesType.anaerobic_training_effect,
            SeriesType.training_load_peak,
            SeriesType.threshold_heart_rate,
            SeriesType.functional_threshold_power,
        )
    }
    rows = db.query(EventRecordAggregate).filter(
        EventRecordAggregate.record_id == record_id,
        EventRecordAggregate.computed_by == ComputedBy.PROVIDER,
        EventRecordAggregate.series_type_definition_id.in_(wanted),
    )
    return {wanted[r.series_type_definition_id]: float(r.sum if r.sum is not None else r.avg) for r in rows}


def test_the_fit_file_s_figures_are_filed_against_its_workout(garmin_247: Garmin247Data, db: Session) -> None:
    _, record_id = _sync(garmin_247, db, make_performance_fit())

    assert _figures(db, record_id) == {
        SeriesType.aerobic_training_effect: 3.4,
        SeriesType.anaerobic_training_effect: 1.2,
        SeriesType.training_load_peak: 142.0,
        SeriesType.threshold_heart_rate: 168.0,
        SeriesType.functional_threshold_power: 265.0,
    }


def test_a_redelivered_file_keeps_one_figure_per_workout(garmin_247: Garmin247Data, db: Session) -> None:
    user = UserFactory()
    conn = UserConnectionFactory(user=user, provider="garmin")
    _push(garmin_247, db, conn.id, user.id, "activityDetails", _activity_details())
    with patch("app.services.providers.garmin.data_247.download_binary_content", return_value=make_performance_fit()):
        _push(garmin_247, db, conn.id, user.id, "activityFiles", _activity_file())
        _push(garmin_247, db, conn.id, user.id, "activityFiles", _activity_file())

    count = (
        db.query(EventRecordAggregate)
        .filter(EventRecordAggregate.series_type_definition_id == get_series_type_id(SeriesType.threshold_heart_rate))
        .count()
    )
    assert count == 1


def test_a_multisport_file_files_nothing(garmin_247: Garmin247Data, db: Session) -> None:
    _, record_id = _sync(garmin_247, db, make_performance_fit(sessions=2))

    assert _figures(db, record_id) == {}


def test_the_workout_listing_returns_them_and_the_zone_basis(
    garmin_247: Garmin247Data, db: Session, client: TestClient
) -> None:
    user_id, _ = _sync(garmin_247, db, make_performance_fit())

    response = client.get(
        f"/api/v1/users/{user_id}/events/workouts",
        headers=api_key_headers(ApiKeyFactory().plain_key),
        params={"start_date": "2026-10-01T00:00:00Z", "end_date": "2026-10-31T00:00:00Z", "include": "zones"},
    )

    assert response.status_code == 200
    (workout,) = response.json()["data"]
    assert workout["provider_metrics"]["threshold_heart_rate"] == {"value": 168.0, "unit": "bpm"}
    assert workout["provider_metrics"]["aerobic_training_effect"] == {"value": 3.4, "unit": "score"}
    assert workout["hr_zones"]["calc_type"] == "percent_lthr"
    assert workout["power_zones"]["calc_type"] == "percent_ftp"
