"""Heart-rate samples by the sensor each one states: strap through the Watch is told apart from the wrist."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy.orm import Session

from app.models import DataSource, User
from app.schemas.enums import ProviderName
from tests.factories import DataPointSeriesFactory, DataSourceFactory, SeriesTypeDefinitionFactory, UserFactory

ENDPOINT = "/api/v1/users/{user_id}/timeseries/heart-rate-sensors"
T0 = datetime(2026, 10, 26, 19, 0, tzinfo=UTC)
LOC = "HKMetadataKeyHeartRateSensorLocation"
CTX = "HKMetadataKeyHeartRateMotionContext"


@pytest.fixture
def watch(db: Session, user: User) -> DataSource:
    ds = DataSourceFactory(
        user=user, provider=ProviderName.APPLE, source="MD Apple Watch Ultra 4", device_model="Watch8,1"
    )
    db.commit()
    return ds


def _hr(db: Session, ds: DataSource, minute: int, meta: dict | None) -> None:
    DataPointSeriesFactory(
        data_source=ds,
        series_type=SeriesTypeDefinitionFactory.get_or_create_heart_rate(),
        recorded_at=T0 + timedelta(minutes=minute),
        provider_metadata=meta,
    )


def _get(client: TestClient, user: User, headers: dict[str, str], **params: object) -> Response:
    return client.get(ENDPOINT.format(user_id=user.id), headers=headers, params=params)


class TestSensorRuns:
    def test_a_switch_from_wrist_to_chest_inside_a_workout_is_two_runs(
        self, client: TestClient, db: Session, user: User, watch: DataSource, api_key_header: dict[str, str]
    ) -> None:
        for m in range(3):
            _hr(db, watch, m, {LOC: "2", CTX: "2"})
        for m in range(3, 7):
            _hr(db, watch, m, {LOC: "1", CTX: "2"})
        _hr(db, watch, 7, {LOC: "2", CTX: "1"})
        _hr(db, watch, 8, None)
        db.commit()

        r = _get(
            client, user, api_key_header, start_time=T0.isoformat(), end_time=(T0 + timedelta(hours=1)).isoformat()
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert [(x["sensor_location"], x["samples"]) for x in body["runs"]] == [
            ("wrist", 3),
            ("chest", 4),
            ("wrist", 1),
            (None, 1),
        ]
        assert body["runs"][1]["active"] == 4
        assert body["runs"][2]["sedentary"] == 1
        # A sample that said nothing is not assumed to be the wrist.
        assert body["runs"][3]["context_not_stated"] == 1
        [source] = body["sources"]
        assert source["data_source_id"] == str(watch.id)
        assert source["by_location"] == {"chest": 4, "wrist": 4, "not_stated": 1}
        assert source["samples"] == 9

    def test_unknown_values_are_kept_verbatim_and_other_sources_stay_apart(
        self, client: TestClient, db: Session, user: User, watch: DataSource, api_key_header: dict[str, str]
    ) -> None:
        airpods = DataSourceFactory(
            user=user, provider=ProviderName.APPLE, source="Michael's AirPods Pro", device_model="AirPods Pro"
        )
        _hr(db, watch, 0, {LOC: "9"})
        _hr(db, airpods, 0, {LOC: "5"})
        db.commit()

        body = _get(
            client, user, api_key_header, start_time=T0.isoformat(), end_time=(T0 + timedelta(hours=1)).isoformat()
        ).json()
        by_ds = {s["data_source_id"]: s["by_location"] for s in body["sources"]}
        assert by_ds == {str(watch.id): {"unrecognised:9": 1}, str(airpods.id): {"ear_lobe": 1}}

        only = _get(
            client,
            user,
            api_key_header,
            start_time=T0.isoformat(),
            end_time=(T0 + timedelta(hours=1)).isoformat(),
            data_source_id=str(airpods.id),
        ).json()
        assert [s["data_source_id"] for s in only["sources"]] == [str(airpods.id)]

    def test_another_users_samples_are_not_read(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        other = DataSourceFactory(user=UserFactory(), provider=ProviderName.APPLE)
        _hr(db, other, 0, {LOC: "1"})
        db.commit()
        body = _get(
            client, user, api_key_header, start_time=T0.isoformat(), end_time=(T0 + timedelta(hours=1)).isoformat()
        ).json()
        assert body == {"start_time": body["start_time"], "end_time": body["end_time"], "sources": [], "runs": []}

    def test_the_window_is_bounded(self, client: TestClient, user: User, api_key_header: dict[str, str]) -> None:
        assert _get(client, user, api_key_header, start_time=T0.isoformat(), end_time=T0.isoformat()).status_code == 400
        long = (T0 + timedelta(days=8)).isoformat()
        assert _get(client, user, api_key_header, start_time=T0.isoformat(), end_time=long).status_code == 400
