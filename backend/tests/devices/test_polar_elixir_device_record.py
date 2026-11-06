"""A Polar Elixir result is dated onto the device it names (``source_device_id``).

Body temperature, SpO2 and wrist ECG results carry the per-device uuid Polar stamps on a
night's sleep. Their samples are not keyed by it, so it was dropped.
"""

from sqlalchemy.orm import Session

from app.models import DataSourceObservation, SeriesSample
from app.services.providers.polar.strategy import PolarStrategy
from tests.factories import UserFactory


def test_spo2_and_ecg_results_record_their_device(db: Session) -> None:
    user = UserFactory()
    data_247 = PolarStrategy().data_247
    samples = data_247.normalize_spo2(
        [
            {
                "source_device_id": "polar-uuid-1",
                "test_time": 1_790_000_000,
                "test_status": "SPO2_TEST_PASSED",
                "blood_oxygen_percent": 97,
            }
        ],
        user.id,
    ) + data_247.normalize_wrist_ecg(
        [{"source_device_id": "polar-uuid-1", "test_time": 1_790_086_400, "average_heart_rate_bpm": 58}],
        user.id,
    )

    data_247._save_timeseries(db, samples)

    [obs] = db.query(DataSourceObservation).filter(DataSourceObservation.user_id == user.id).all()
    assert obs.device_local_identifier == "polar-uuid-1"
    assert obs.records_reported == 2
    stored = db.query(SeriesSample).filter(SeriesSample.data_source_id == obs.data_source_id).all()
    assert {s.provider_metadata["source_device_id"] for s in stored} == {"polar-uuid-1"}


def test_a_result_naming_no_device_records_nothing(db: Session) -> None:
    user = UserFactory()
    data_247 = PolarStrategy().data_247
    samples = data_247.normalize_spo2(
        [{"test_time": 1_790_000_000, "test_status": "SPO2_TEST_PASSED", "blood_oxygen_percent": 97}], user.id
    )

    data_247._save_timeseries(db, samples)

    assert db.query(DataSourceObservation).filter(DataSourceObservation.user_id == user.id).count() == 0
