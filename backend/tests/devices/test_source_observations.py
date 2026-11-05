"""What a sync said about the hardware and software behind a source is kept, and dated.

A data source keeps what identifies it. The accessory's own name, its firmware, the
writing app's version and the phone's OS change over a study without changing which
stream it is, and were dropped. These pin that each distinct description becomes one
row, spanning the records that carried it.
"""

from datetime import datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.models import DataSource, DataSourceObservation, User, UserConnection
from app.schemas.enums import DeviceType
from app.services.sdk.import_service import ImportService
from app.utils.connection_context import active_connection
from tests.factories import UserConnectionFactory, UserFactory

AIRPODS = {
    "name": "Bluetooth Device",
    "appId": "com.apple.heartratecoordinatord",
    "deviceName": "Michael's AirPods Pro",
    "deviceManufacturer": "Apple Inc.",
    "deviceModel": "AirPods Pro",
    "deviceSoftwareVersion": "8A356",
    "operatingSystemVersion": {"majorVersion": 26, "minorVersion": 1, "patchVersion": 0},
}


def _hr(start: str, end: str, source: dict[str, Any], value: int = 120) -> dict[str, Any]:
    return {
        "type": "HKQuantityTypeIdentifierHeartRate",
        "unit": "count/min",
        "value": value,
        "startDate": start,
        "endDate": end,
        "source": source,
    }


def _sync(db: Session, user: User, connection: UserConnection, records: list[dict[str, Any]]) -> None:
    payload = {
        "provider": "apple",
        "sdkVersion": "1.0.0",
        "syncTimestamp": "2026-10-26T14:00:00Z",
        "data": {"records": records},
    }
    with active_connection(connection.id):
        ImportService().load_data(db, payload, str(user.id))


@pytest.fixture
def user(db: Session) -> User:
    return UserFactory()


@pytest.fixture
def connection(db: Session, user: User) -> UserConnection:
    connection = UserConnectionFactory(user=user, provider="apple")
    db.flush()
    return connection


def _observations(db: Session, user: User) -> list[DataSourceObservation]:
    return (
        db.query(DataSourceObservation)
        .filter(DataSourceObservation.user_id == user.id)
        .order_by(DataSourceObservation.first_recorded_at)
        .all()
    )


class TestWhatIsKept:
    def test_the_accessory_is_described_and_dated(self, db: Session, user: User, connection: UserConnection) -> None:
        _sync(
            db,
            user,
            connection,
            [
                _hr("2026-10-26T13:23:24Z", "2026-10-26T13:23:24Z", AIRPODS),
                _hr("2026-10-26T13:28:29Z", "2026-10-26T13:28:29Z", AIRPODS),
            ],
        )

        [obs] = _observations(db, user)
        assert obs.source_name == "Bluetooth Device"
        assert obs.source_bundle_id == "com.apple.heartratecoordinatord"
        assert obs.device_name == "Michael's AirPods Pro"
        assert obs.device_manufacturer == "Apple Inc."
        assert obs.device_model == "AirPods Pro"
        assert obs.device_software_version == "8A356"
        assert obs.source_os_version == "26.1.0"
        assert obs.records_reported == 2
        assert obs.first_recorded_at == datetime.fromisoformat("2026-10-26T13:23:24+00:00")
        assert obs.last_recorded_at == datetime.fromisoformat("2026-10-26T13:28:29+00:00")
        # Null is "not said": the SDK sent no firmware, so none is recorded.
        assert obs.device_firmware_version is None

        source = db.get(DataSource, obs.data_source_id)
        assert source is not None
        assert source.source == "Michael's AirPods Pro"
        assert source.source_app_id == "com.apple.heartratecoordinatord"
        assert source.device_type == DeviceType.HEADPHONES.value

    def test_an_update_is_a_new_row_ending_where_the_next_begins(
        self, db: Session, user: User, connection: UserConnection
    ) -> None:
        _sync(db, user, connection, [_hr("2026-10-20T10:00:00Z", "2026-10-20T10:00:00Z", AIRPODS)])
        updated = {**AIRPODS, "deviceSoftwareVersion": "8B21"}
        _sync(db, user, connection, [_hr("2026-10-27T10:00:00Z", "2026-10-27T10:00:00Z", updated)])

        old, new = _observations(db, user)
        assert old.data_source_id == new.data_source_id
        assert (old.device_software_version, new.device_software_version) == ("8A356", "8B21")
        assert old.last_recorded_at is not None
        assert new.first_recorded_at is not None
        assert old.last_recorded_at < new.first_recorded_at

    def test_a_late_history_sync_widens_the_span_and_adds_to_the_count(
        self, db: Session, user: User, connection: UserConnection
    ) -> None:
        _sync(db, user, connection, [_hr("2026-10-26T13:00:00Z", "2026-10-26T13:00:00Z", AIRPODS)])
        _sync(db, user, connection, [_hr("2026-10-01T09:00:00Z", "2026-10-01T09:00:00Z", AIRPODS)])

        [obs] = _observations(db, user)
        assert obs.first_recorded_at == datetime.fromisoformat("2026-10-01T09:00:00+00:00")
        assert obs.last_recorded_at == datetime.fromisoformat("2026-10-26T13:00:00+00:00")
        assert obs.records_reported == 2

    def test_fields_the_sdk_does_not_send_yet_are_recorded_when_it_does(
        self, db: Session, user: User, connection: UserConnection
    ) -> None:
        strap = {
            "name": "Bluetooth Device",
            "appId": "com.apple.BTLEServer",
            "deviceName": "Polar H10 8E1F2C3A",
            "deviceManufacturer": "Polar Electro Oy",
            "deviceModel": "H10",
            "deviceFirmwareVersion": "5.0.0",
            "deviceLocalIdentifier": "6B2E0F0C-1D2A-4F5B-9A3C-7E8D9F0A1B2C",
            "version": "26.1",
        }
        _sync(db, user, connection, [_hr("2026-10-26T19:06:51Z", "2026-10-26T19:06:51Z", strap)])

        [obs] = _observations(db, user)
        assert obs.device_firmware_version == "5.0.0"
        assert obs.device_local_identifier == "6B2E0F0C-1D2A-4F5B-9A3C-7E8D9F0A1B2C"
        assert obs.source_version == "26.1"
        source = db.get(DataSource, obs.data_source_id)
        assert source is not None
        # Named and typed by the accessory, not by "Bluetooth Device".
        assert source.source == "Polar H10 8E1F2C3A"
        assert source.device_type == DeviceType.CHEST_STRAP.value

    def test_two_accessories_from_one_service_are_described_separately(
        self, db: Session, user: User, connection: UserConnection
    ) -> None:
        strap = {**AIRPODS, "appId": "com.apple.BTLEServer", "deviceName": "Polar H10", "deviceModel": "H10"}
        _sync(
            db,
            user,
            connection,
            [
                _hr("2026-10-26T13:23:24Z", "2026-10-26T13:23:24Z", AIRPODS),
                _hr("2026-10-26T19:06:51Z", "2026-10-26T19:06:51Z", strap),
            ],
        )
        observations = _observations(db, user)
        assert len({o.data_source_id for o in observations}) == 2
