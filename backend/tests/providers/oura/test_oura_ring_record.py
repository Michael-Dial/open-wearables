"""Which Oura ring recorded each night, and when a participant changed rings.

Oura stamps ``ring_id`` on every sleep document. These pin that it is kept on the night,
on the in-sleep heart-rate series, and as a dated record per ring (one observation
spanning the nights that named it), and that a re-pull of history adds the stamp to
nights stored before it was read.
"""

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import DataSourceObservation, EventRecord, SeriesSample, User
from app.services.event_record_service import merge_provider_metadata
from app.services.providers.oura.data_247 import Oura247Data
from app.services.providers.oura.strategy import OuraStrategy
from tests.factories import UserFactory


def _night(sleep_id: str, start: str, end: str, ring_id: str | None) -> dict[str, Any]:
    night: dict[str, Any] = {
        "id": sleep_id,
        "type": "long_sleep",
        "bedtime_start": start,
        "bedtime_end": end,
        "time_in_bed": 28800,
        "deep_sleep_duration": 5400,
        "light_sleep_duration": 14400,
        "rem_sleep_duration": 6000,
        "awake_time": 3000,
        "heart_rate": {"interval": 300, "items": [52, 50], "timestamp": start},
    }
    if ring_id is not None:
        night["ring_id"] = ring_id
    return night


def _save(db: Session, user: User, nights: list[dict[str, Any]]) -> None:
    data_247 = OuraStrategy().data_247
    assert isinstance(data_247, Oura247Data)
    data_247.save_sleep_data(db, user.id, data_247.normalize_sleeps(nights, user.id))


def _rings(db: Session, user: User) -> list[DataSourceObservation]:
    return (
        db.query(DataSourceObservation)
        .filter(DataSourceObservation.user_id == user.id)
        .order_by(DataSourceObservation.first_recorded_at)
        .all()
    )


def test_each_ring_is_dated_by_the_nights_that_named_it(db: Session) -> None:
    user = UserFactory()
    _save(
        db,
        user,
        [
            _night("s1", "2026-10-01T23:00:00+00:00", "2026-10-02T07:00:00+00:00", "ring-A"),
            _night("s2", "2026-10-02T23:00:00+00:00", "2026-10-03T07:00:00+00:00", "ring-A"),
            _night("s3", "2026-10-03T23:00:00+00:00", "2026-10-04T07:00:00+00:00", "ring-B"),
        ],
    )

    old, new = _rings(db, user)
    assert (old.device_local_identifier, new.device_local_identifier) == ("ring-A", "ring-B")
    assert old.source_name == new.source_name == "Oura"
    assert old.data_source_id == new.data_source_id
    assert (old.records_reported, new.records_reported) == (2, 1)
    assert old.first_recorded_at == datetime.fromisoformat("2026-10-01T23:00:00+00:00")
    assert old.last_recorded_at == datetime.fromisoformat("2026-10-03T07:00:00+00:00")
    assert new.first_recorded_at == datetime.fromisoformat("2026-10-03T23:00:00+00:00")
    # Only what the night states: no generation or firmware from today's ring_configuration.
    assert old.device_model is None
    assert old.device_firmware_version is None


def test_the_night_and_its_heart_rate_carry_the_ring(db: Session) -> None:
    user = UserFactory()
    _save(db, user, [_night("s1", "2026-10-01T23:00:00+00:00", "2026-10-02T07:00:00+00:00", "ring-A")])

    sleep = db.query(EventRecord).filter(EventRecord.category == "sleep").one()
    assert sleep.provider_metadata == {"ring_ids": ["ring-A"]}

    hr = db.query(SeriesSample).filter(SeriesSample.data_source_id == sleep.data_source_id).all()
    assert hr
    assert {s.provider_metadata.get("ring_id") for s in hr} == {"ring-A"}


def test_a_night_without_a_ring_id_records_no_ring(db: Session) -> None:
    user = UserFactory()
    _save(db, user, [_night("s1", "2026-10-01T23:00:00+00:00", "2026-10-02T07:00:00+00:00", None)])

    assert _rings(db, user) == []
    sleep = db.query(EventRecord).filter(EventRecord.category == "sleep").one()
    assert sleep.provider_metadata is None


def test_a_history_re_pull_stamps_nights_stored_before_the_ring_was_read(db: Session) -> None:
    user = UserFactory()
    night = _night("s1", "2026-10-01T23:00:00+00:00", "2026-10-02T07:00:00+00:00", None)
    _save(db, user, [night])
    _save(db, user, [{**night, "ring_id": "ring-A"}])

    sleep = db.query(EventRecord).filter(EventRecord.category == "sleep").one()
    assert sleep.provider_metadata == {"ring_ids": ["ring-A"]}
    [ring] = _rings(db, user)
    assert ring.device_local_identifier == "ring-A"


class TestMergeProviderMetadata:
    def test_lists_are_unioned_so_two_rings_on_one_night_stay_two(self) -> None:
        assert merge_provider_metadata({"ring_ids": ["A"]}, {"ring_ids": ["B", "A"]}) == {"ring_ids": ["A", "B"]}

    def test_a_scalar_takes_the_newer_value(self) -> None:
        assert merge_provider_metadata({"x": 1, "keep": True}, {"x": 2}) == {"x": 2, "keep": True}

    def test_nothing_on_either_side(self) -> None:
        assert merge_provider_metadata(None, None) is None
        assert merge_provider_metadata(None, {"ring_ids": ["A"]}) == {"ring_ids": ["A"]}
        assert merge_provider_metadata({"ring_ids": ["A"]}, None) == {"ring_ids": ["A"]}
