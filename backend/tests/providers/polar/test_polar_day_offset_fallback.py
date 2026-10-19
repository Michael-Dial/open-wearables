"""A Polar day with no sleep record still gets its continuous HR and intraday steps.

Both arrive on a zoneless local clock, and the fork places them at true UTC with the
offset Polar stated on that day's sleep record. A day the wearer did not sleep in the
watch has no such record, and its samples used to be skipped. The offset now falls back
to the one ``PolarV4Data.ppi_utc_offsets`` resolves for v4 PPI: a stored Polar record
(an exercise states its own offset) near the day, else the v4 account timezone. A day
none of these can place is still skipped rather than stored at a guess.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.models import DataPointSeries, DataSource, User
from app.schemas.enums import ProviderName, SeriesType, get_series_type_id
from app.schemas.providers.polar import PolarWebhookEventType
from app.services.providers.polar.data_247 import Polar247Data
from app.services.providers.polar.strategy import PolarStrategy
from app.services.providers.polar.v4_data import PolarV4Data
from tests.factories import DataSourceFactory, EventRecordFactory, UserFactory

DAY = "2026-09-10"

# GET /v3/users/continuous-heart-rate/{date}
CHR_RESPONSE: dict[str, Any] = {
    "polar_user": "https://www.polaraccesslink.com/v3/users/475",
    "date": DAY,
    "heart_rate_samples": [
        {"heart_rate": 71, "sample_time": "14:05:00"},
        {"heart_rate": 74, "sample_time": "14:10:00"},
    ],
}

# GET /v3/users/activities/{date}?steps=true: the row states no offset either.
ACTIVITY_RESPONSE: dict[str, Any] = {
    "polar_user": "https://www.polaraccesslink.com/v3/users/475",
    "date": DAY,
    "start_time": f"{DAY}T00:00:00",
    "end_time": f"{DAY}T23:59:59",
    "steps": 6_210,
    "active_calories": 310,
    "samples": {
        "steps": {
            "interval_ms": 600_000,
            "total_steps": 6_210,
            "samples": [{"steps": 150, "timestamp": f"{DAY}T14:00"}, {"steps": 0, "timestamp": f"{DAY}T14:10"}],
        }
    },
}


def _polar_api_without_sleep(db: Any, user_id: Any, path: str, params: Any = None) -> Any:
    if path == "/v3/users/sleep/available":
        return {"available": []}  # the watch was not worn to bed
    if path.startswith("/v3/users/continuous-heart-rate/"):
        return CHR_RESPONSE
    if path.startswith("/v3/users/activities/"):
        return ACTIVITY_RESPONSE
    raise AssertionError(f"unexpected request {path}")


def _exercise_with_offset(db: Session, user: User, start: datetime, zone_offset: str) -> None:
    """A stored Polar exercise: AccessLink states its start_time_utc_offset, kept as zone_offset."""
    source = DataSourceFactory(user=user, provider=ProviderName.POLAR, source=ProviderName.POLAR.value)
    EventRecordFactory(data_source=source, start_datetime=start, zone_offset=zone_offset)
    db.flush()


def _rows(db: Session, user_id: UUID, series_type: SeriesType) -> list[DataPointSeries]:
    return (
        db.query(DataPointSeries)
        .join(DataSource, DataSource.id == DataPointSeries.data_source_id)
        .filter(
            DataSource.user_id == user_id,
            DataPointSeries.series_type_definition_id == get_series_type_id(series_type),
        )
        .order_by(DataPointSeries.recorded_at)
        .all()
    )


@pytest.fixture
def data_247() -> Polar247Data:
    data = PolarStrategy().data_247
    assert isinstance(data, Polar247Data)
    return data


def _webhook(data_247: Polar247Data, db: Session, user: User, event: PolarWebhookEventType, path: str) -> None:
    with (
        patch.object(Polar247Data, "_make_api_request", side_effect=_polar_api_without_sleep),
        patch.object(PolarV4Data, "_get", return_value=None),  # no v4 account timezone
    ):
        data_247.fetch_and_save_from_webhook(db, UUID(str(user.id)), event, path)


class TestADayWithNoSleepRecord:
    def test_continuous_hr_is_placed_by_a_nearby_exercise(self, data_247: Polar247Data, db: Session) -> None:
        user = UserFactory()
        # An evening run the day before: 18:30 EDT.
        _exercise_with_offset(db, user, datetime(2026, 9, 9, 22, 30, tzinfo=timezone.utc), "-04:00")

        _webhook(
            data_247, db, user, PolarWebhookEventType.CONTINUOUS_HEART_RATE, f"/v3/users/continuous-heart-rate/{DAY}"
        )

        rows = _rows(db, user.id, SeriesType.heart_rate)
        # 14:05 on the wearer's EDT clock is 18:05 UTC.
        assert [(r.recorded_at.isoformat(), r.zone_offset, int(r.value)) for r in rows] == [
            ("2026-09-10T18:05:00+00:00", "-04:00", 71),
            ("2026-09-10T18:10:00+00:00", "-04:00", 74),
        ]

    def test_intraday_steps_are_placed_by_a_nearby_exercise(self, data_247: Polar247Data, db: Session) -> None:
        user = UserFactory()
        _exercise_with_offset(db, user, datetime(2026, 9, 9, 22, 30, tzinfo=timezone.utc), "-04:00")

        _webhook(data_247, db, user, PolarWebhookEventType.ACTIVITY_SUMMARY, f"/v3/users/activities/{DAY}")

        rows = _rows(db, user.id, SeriesType.steps)
        assert [(r.recorded_at.isoformat(), r.is_daily_total, int(r.value)) for r in rows] == [
            # The day's total keeps upstream's stamp: local midnight read as UTC.
            ("2026-09-10T00:00:00+00:00", True, 6_210),
            ("2026-09-10T18:00:00+00:00", False, 150),
            ("2026-09-10T18:10:00+00:00", False, 0),
        ]

    def test_a_day_nothing_can_place_is_still_skipped_not_guessed(self, data_247: Polar247Data, db: Session) -> None:
        user = UserFactory()

        _webhook(
            data_247, db, user, PolarWebhookEventType.CONTINUOUS_HEART_RATE, f"/v3/users/continuous-heart-rate/{DAY}"
        )
        _webhook(data_247, db, user, PolarWebhookEventType.ACTIVITY_SUMMARY, f"/v3/users/activities/{DAY}")

        assert _rows(db, user.id, SeriesType.heart_rate) == []
        assert [r.is_daily_total for r in _rows(db, user.id, SeriesType.steps)] == [True]


class TestADayWithASleepRecord:
    def test_its_totals_and_placed_steps_are_written_together(self, data_247: Polar247Data, db: Session) -> None:
        """The totals' stamp was naive and the samples' aware: one batch with both failed to write at all."""
        user = UserFactory()
        sleep = {"date": DAY, "sleep_start_time": f"{DAY}T00:40:00-04:00", "sleep_end_time": f"{DAY}T07:05:00-04:00"}

        def api(db: Any, user_id: Any, path: str, params: Any = None) -> Any:
            if path == "/v3/users/sleep/available":
                return {"available": [{"date": DAY}]}
            if path.startswith("/v3/users/sleep/"):
                return sleep
            return _polar_api_without_sleep(db, user_id, path, params)

        with patch.object(Polar247Data, "_make_api_request", side_effect=api):
            data_247.fetch_and_save_from_webhook(
                db, UUID(str(user.id)), PolarWebhookEventType.ACTIVITY_SUMMARY, f"/v3/users/activities/{DAY}"
            )

        rows = _rows(db, user.id, SeriesType.steps)
        assert [(r.recorded_at.isoformat(), r.is_daily_total) for r in rows] == [
            ("2026-09-10T00:00:00+00:00", True),
            ("2026-09-10T18:00:00+00:00", False),
            ("2026-09-10T18:10:00+00:00", False),
        ]


class TestTheSleepRecordStillComesFirst:
    def test_the_nights_own_offset_beats_the_fallback(self, data_247: Polar247Data) -> None:
        sleep_starts = {DAY: datetime.fromisoformat("2026-09-09T23:10:00-04:00")}
        fallback = {date(2026, 9, 10): 120}  # a stale +02:00 from before a trip

        samples = data_247.normalize_continuous_hr([CHR_RESPONSE], uuid4(), sleep_starts, fallback)

        assert samples[0].recorded_at == datetime(2026, 9, 10, 18, 5, tzinfo=timezone.utc)

    def test_the_fallback_places_a_day_the_sleep_records_cannot(self, data_247: Polar247Data) -> None:
        fallback = {date(2026, 9, 10): 120}

        samples = data_247.normalize_daily_activity([ACTIVITY_RESPONSE], uuid4(), {}, fallback)

        intraday = [s for s in samples if s.series_type == SeriesType.steps and not s.is_daily_total]
        assert [s.recorded_at for s in intraday] == [
            datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
            datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=10),
        ]
        assert {s.zone_offset for s in intraday} == {"+02:00"}
