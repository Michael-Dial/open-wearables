"""A Google Health reading's dataSource is dated onto the data source it was written to."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from app.models import DataSourceObservation
from app.services.providers.google_health.data_247 import GoogleHealth247Data
from app.services.providers.google_health.metrics import METRICS
from app.services.timeseries_service import timeseries_service
from tests.factories import UserFactory

WINDOW = (datetime(2026, 5, 1, tzinfo=timezone.utc), datetime(2026, 5, 3, tzinfo=timezone.utc))
HEART_RATE = next(m for m in METRICS if m.data_type == "heart-rate")


def _point(at: str, bpm: int, data_source: dict) -> dict:
    return {
        "heartRate": {"sampleTime": {"physicalTime": at, "utcOffset": "0s"}, "beatsPerMinute": bpm},
        "dataSource": data_source,
    }


def test_measured_and_manual_readings_are_recorded_apart(db: Session) -> None:
    user = UserFactory()
    data_247 = GoogleHealth247Data(oauth=MagicMock(), connection_repo=MagicMock(), api_base_url="https://x")
    watch = {
        "recordingMethod": "PASSIVELY_MEASURED",
        "platform": "FITBIT",
        "device": {"displayName": "Pixel Watch 3", "formFactor": "WATCH"},
    }
    points = [
        _point("2026-05-02T07:00:00Z", 58, watch),
        _point("2026-05-02T07:05:00Z", 60, watch),
        _point("2026-05-02T09:00:00Z", 72, {**watch, "recordingMethod": "MANUAL"}),
    ]
    with patch.object(data_247, "_fetch_points", return_value=iter(points)):
        samples = data_247._native_samples(MagicMock(), user.id, HEART_RATE, *WINDOW)
    timeseries_service.bulk_create_samples(db, samples)

    data_247._record_sources(db, samples)

    rows = (
        db.query(DataSourceObservation)
        .filter(DataSourceObservation.user_id == user.id)
        .order_by(DataSourceObservation.first_recorded_at)
        .all()
    )
    assert [(r.recording_method, r.records_reported) for r in rows] == [("PASSIVELY_MEASURED", 2), ("MANUAL", 1)]
    assert {r.device_model for r in rows} == {"Pixel Watch 3"}
    assert {r.source_name for r in rows} == {"FITBIT"}
    assert {r.device_type_reported for r in rows} == {"WATCH"}


def test_paired_devices_are_registered_by_googles_id_without_the_mac(db: Session) -> None:
    from app.models import Device, DeviceIdentity

    user = UserFactory()
    data_247 = GoogleHealth247Data(oauth=MagicMock(), connection_repo=MagicMock(), api_base_url="https://x")
    listing = {
        "pairedDevices": [
            {
                "name": "users/me/pairedDevices/abc123",
                "deviceVersion": "Charge 6",
                "macAddress": "AA:BB:CC:DD:EE:FF",
                "deviceType": "TRACKER",
            },
            {"name": "users/me/pairedDevices/def456", "deviceVersion": "Charge 6"},
        ]
    }
    target = "app.services.providers.google_health.data_247.make_authenticated_request"
    with patch(target, return_value=listing):
        assert data_247.save_paired_devices(db, user.id) == 2
        # A second sync finds both by their claim and registers nothing new.
        assert data_247.save_paired_devices(db, user.id) == 0

    devices = db.query(Device).filter(Device.user_id == user.id).all()
    assert len(devices) == 2
    claims = db.query(DeviceIdentity).filter(DeviceIdentity.user_id == user.id).all()
    assert {c.id_value for c in claims if c.id_kind == "google_paired_device"} == {"abc123", "def456"}
    assert all("AA:BB" not in c.id_value for c in claims)


def test_an_unexpected_paired_devices_body_registers_nothing(db: Session) -> None:
    user = UserFactory()
    data_247 = GoogleHealth247Data(oauth=MagicMock(), connection_repo=MagicMock(), api_base_url="https://x")
    target = "app.services.providers.google_health.data_247.make_authenticated_request"
    with patch(target, return_value={"devices": "?"}):
        assert data_247.save_paired_devices(db, user.id) == 0
