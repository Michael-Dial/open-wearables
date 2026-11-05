"""The source-observations endpoint, and the writer id on the device listing."""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import DataSourceObservation, User, UserConnection
from tests.devices.test_source_observations import AIRPODS, _hr, _sync
from tests.factories import UserConnectionFactory


@pytest.fixture
def connection(db: Session, user: User) -> UserConnection:
    connection = UserConnectionFactory(user=user, provider="apple")
    db.flush()
    return connection


def _observations(db: Session, user: User) -> list[DataSourceObservation]:
    return db.query(DataSourceObservation).filter(DataSourceObservation.user_id == user.id).all()


class TestEndpoint:
    def test_lists_a_users_observations_filtered_by_source(
        self,
        client: TestClient,
        db: Session,
        user: User,
        connection: UserConnection,
        api_key_header: dict[str, str],
    ) -> None:
        _sync(db, user, connection, [_hr("2026-10-26T13:23:24Z", "2026-10-26T13:23:24Z", AIRPODS)])
        [obs] = _observations(db, user)

        url = f"/api/v1/users/{user.id}/devices/source-observations"
        body = client.get(url, headers=api_key_header).json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["device_name"] == "Michael's AirPods Pro"
        assert item["records_reported"] == 1

        other = client.get(url, headers=api_key_header, params={"data_source_id": str(UUID(int=1))}).json()
        assert other["total"] == 0

        mine = client.get(url, headers=api_key_header, params={"data_source_id": str(obs.data_source_id)}).json()
        assert mine["total"] == 1

    def test_the_device_listing_carries_the_writer_id(
        self,
        client: TestClient,
        db: Session,
        user: User,
        connection: UserConnection,
        api_key_header: dict[str, str],
    ) -> None:
        _sync(db, user, connection, [_hr("2026-10-26T13:23:24Z", "2026-10-26T13:23:24Z", AIRPODS)])
        body = client.get(f"/api/v1/users/{user.id}/devices", headers=api_key_header).json()
        sources = [s for d in body["items"] for s in d["data_sources"]]
        assert [s["source_app_id"] for s in sources] == ["com.apple.heartratecoordinatord"]
