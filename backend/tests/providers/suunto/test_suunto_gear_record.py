"""A Suunto watch is described, and dated, by the workouts that carried its gear block.

The gear block names the unit (serialNumber) and its hardware and software versions.
Only the name and software version reached the data source, and each sync overwrote
them; these pin that each distinct description is kept with the span it covered.
"""

from typing import Any

from sqlalchemy.orm import Session

from app.models import DataSourceObservation, User
from app.services.providers.suunto.workouts import SuuntoWorkouts
from tests.factories import UserFactory

RACE_2 = {
    "manufacturer": "Suunto",
    "name": "Suunto Race 2",
    "displayName": None,
    "serialNumber": "TEST-SERIAL-0001",
    "softwareVersion": "2.53.42",
    "hardwareVersion": "Sailfish_RevA1",
    "productType": "SPORT_WATCH",
}


def _workout(workout_id: int, start_ms: int, gear: dict[str, Any] | None) -> dict[str, Any]:
    workout: dict[str, Any] = {"workoutId": workout_id, "activityId": 3, "startTime": start_ms, "totalTime": 600.0}
    if gear is not None:
        # Race 2 and later ship gear inside SummaryExtension, as the live capture does.
        workout["extensions"] = [{"type": "SummaryExtension", "gear": gear}]
    return workout


def _observations(db: Session, user: User) -> list[DataSourceObservation]:
    return (
        db.query(DataSourceObservation)
        .filter(DataSourceObservation.user_id == user.id)
        .order_by(DataSourceObservation.first_recorded_at)
        .all()
    )


def _push(db: Session, user: User, workout: dict[str, Any]) -> None:
    from app.services.providers.suunto.strategy import SuuntoStrategy

    workouts = SuuntoStrategy().workouts
    assert isinstance(workouts, SuuntoWorkouts)
    workouts.process_push_activity(db, user.id, workout)


def test_the_watch_is_described_by_serial_and_versions(db: Session) -> None:
    user = UserFactory()
    _push(db, user, _workout(1, 1_780_000_000_000, RACE_2))

    [obs] = _observations(db, user)
    assert obs.device_serial_number == "TEST-SERIAL-0001"
    assert obs.device_model == "Suunto Race 2"
    assert obs.device_manufacturer == "Suunto"
    assert obs.device_hardware_version == "Sailfish_RevA1"
    assert obs.device_software_version == "2.53.42"
    assert obs.device_type_reported == "SPORT_WATCH"
    # displayName was null: not said, so not invented from the model name.
    assert obs.device_name is None


def test_a_firmware_update_starts_a_new_span(db: Session) -> None:
    user = UserFactory()
    _push(db, user, _workout(1, 1_780_000_000_000, RACE_2))
    _push(db, user, _workout(2, 1_780_600_000_000, {**RACE_2, "softwareVersion": "2.54.10"}))

    old, new = _observations(db, user)
    assert (old.device_software_version, new.device_software_version) == ("2.53.42", "2.54.10")
    assert old.device_serial_number == new.device_serial_number == "TEST-SERIAL-0001"
    assert old.last_recorded_at is not None
    assert new.first_recorded_at is not None
    assert old.last_recorded_at < new.first_recorded_at


def test_a_workout_without_gear_records_nothing(db: Session) -> None:
    user = UserFactory()
    _push(db, user, _workout(1, 1_780_000_000_000, None))
    assert _observations(db, user) == []
