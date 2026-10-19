"""Garmin epochs are not lost to the series key (data_source, series_type, recorded_at).

Garmin sends one epoch row per 15-minute slot *per activity type*: a slot in which the
wearer sat for ten minutes and walked for five arrives as a SEDENTARY row and a WALKING
row with the same ``startTimeInSeconds``. Each row's steps and kcal cover only its own
activity type. Stored one by one, every row of a slot lands on the same key and only the
last survives, so the slot's steps and kcal undercount.

These tests run the webhook batch path against a real database, because the loss only
exists in what the database ends up holding.
"""

from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DataPointSeries, DataSource
from app.repositories.user_connection_repository import UserConnectionRepository
from app.schemas.enums import SeriesType, get_series_type_id
from app.services.providers.garmin.data_247 import Garmin247Data
from app.services.providers.garmin.oauth import GarminOAuth
from app.utils.connection_context import active_connection
from tests.factories import UserConnectionFactory, UserFactory

OFFSET = -14_400  # EDT
DAY_START = 1_791_259_200  # 2026-10-05 00:00 local (-04:00) in UTC seconds
SLOT = DAY_START + 10 * 3600  # 10:00 local


def _epoch(slot_start: int, activity_type: str, code: int, **metrics: Any) -> dict[str, Any]:
    """One epoch row in the shape Garmin pushes (Health API ``epochs`` summary)."""
    return {
        "userId": "4aacafe82427c251df9c9592d0c06768",
        "summaryId": f"x153a5e8-{slot_start:x}-{code}",
        "activityType": activity_type,
        "startTimeInSeconds": slot_start,
        "startTimeOffsetInSeconds": OFFSET,
        "durationInSeconds": 900,
        **metrics,
    }


SEDENTARY = _epoch(
    SLOT,
    "SEDENTARY",
    0,
    activeTimeInSeconds=600,
    steps=12,
    distanceInMeters=8.4,
    activeKilocalories=3,
    met=1.0,
    intensity="SEDENTARY",
    meanMotionIntensity=0.5,
    maxMotionIntensity=2.0,
    meanHeartRateInBeatsPerMinute=62,
    maxHeartRateInBeatsPerMinute=70,
)
WALKING = _epoch(
    SLOT,
    "WALKING",
    6,
    activeTimeInSeconds=300,
    steps=480,
    distanceInMeters=372.0,
    activeKilocalories=21,
    met=3.3,
    intensity="ACTIVE",
    meanMotionIntensity=4.0,
    maxMotionIntensity=7.0,
    meanHeartRateInBeatsPerMinute=95,
    maxHeartRateInBeatsPerMinute=104,
)


def _rows(db: Session, user_id: UUID, series_type: SeriesType) -> dict[int, Decimal]:
    rows = db.execute(
        select(DataPointSeries.recorded_at, DataPointSeries.value)
        .join(DataSource, DataSource.id == DataPointSeries.data_source_id)
        .where(
            DataSource.user_id == user_id,
            DataPointSeries.series_type_definition_id == get_series_type_id(series_type),
        )
    ).all()
    return {int(r.recorded_at.timestamp()): r.value for r in rows}


@pytest.fixture
def garmin_247() -> Garmin247Data:
    oauth = GarminOAuth(
        user_repo=MagicMock(),
        connection_repo=UserConnectionRepository(),
        provider_name="garmin",
        api_base_url="https://apis.garmin.com",
    )
    return Garmin247Data(provider_name="garmin", api_base_url="https://apis.garmin.com", oauth=oauth)


def _push(garmin_247: Garmin247Data, db: Session, kind: str, items: list[dict[str, Any]]) -> UUID:
    user = UserFactory()
    conn = UserConnectionFactory(user=user, provider="garmin")
    with active_connection(conn.id):
        garmin_247.process_items_batch(db, user.id, kind, items)
    return user.id


class TestEpochsPerActivityType:
    def test_a_slots_activity_types_add_up(self, garmin_247: Garmin247Data, db: Session) -> None:
        user_id = _push(garmin_247, db, "epochs", [SEDENTARY, WALKING])

        assert _rows(db, user_id, SeriesType.steps) == {SLOT: Decimal(12 + 480)}
        assert _rows(db, user_id, SeriesType.active_energy) == {SLOT: Decimal(3 + 21)}

    def test_the_slots_heart_rate_is_weighted_by_time_in_each_type(
        self, garmin_247: Garmin247Data, db: Session
    ) -> None:
        user_id = _push(garmin_247, db, "epochs", [SEDENTARY, WALKING])

        # (62 * 600 + 95 * 300) / 900
        assert _rows(db, user_id, SeriesType.heart_rate) == {SLOT: Decimal("73.000")}

    def test_order_within_the_push_does_not_matter(self, garmin_247: Garmin247Data, db: Session) -> None:
        user_id = _push(garmin_247, db, "epochs", [WALKING, SEDENTARY])

        assert _rows(db, user_id, SeriesType.steps) == {SLOT: Decimal(492)}

    def test_slots_stay_apart(self, garmin_247: Garmin247Data, db: Session) -> None:
        later = _epoch(SLOT + 900, "SEDENTARY", 0, activeTimeInSeconds=900, steps=0, activeKilocalories=1)
        user_id = _push(garmin_247, db, "epochs", [SEDENTARY, WALKING, later])

        assert _rows(db, user_id, SeriesType.steps) == {SLOT: Decimal(492), SLOT + 900: Decimal(0)}

    def test_a_pull_adds_up_the_same_way(self, garmin_247: Garmin247Data) -> None:
        normalized = garmin_247.normalize_epochs([SEDENTARY, WALKING], uuid4())

        assert [s["value"] for s in normalized["steps"]] == [492]
        assert [s["value"] for s in normalized["energy"]] == [24]
        # Every row in a slot shares one per-slot id: the activity-type suffix is dropped.
        assert {s["external_id"] for s in normalized["steps"]} == {f"x153a5e8-{SLOT:x}"}


DAILY = {
    "userId": "4aacafe82427c251df9c9592d0c06768",
    "summaryId": "x153a5e8-68e1ee40-15180-0",
    "calendarDate": "2026-10-05",
    "activityType": "WALKING",
    "startTimeInSeconds": DAY_START,
    "startTimeOffsetInSeconds": OFFSET,
    "durationInSeconds": 86_400,
    "steps": 9_874,
    "distanceInMeters": 7_420.0,
    "activeKilocalories": 512,
    "bmrKilocalories": 1_690,
    "restingHeartRateInBeatsPerMinute": 54,
}
# The day's first slot starts on the same instant as the daily summary: local midnight.
FIRST_EPOCH = _epoch(
    DAY_START, "SEDENTARY", 0, activeTimeInSeconds=900, steps=7, activeKilocalories=2, meanHeartRateInBeatsPerMinute=58
)


def _rows_with_flag(db: Session, user_id: UUID, series_type: SeriesType) -> dict[int, tuple[Decimal, bool | None]]:
    rows = db.execute(
        select(DataPointSeries.recorded_at, DataPointSeries.value, DataPointSeries.is_daily_total)
        .join(DataSource, DataSource.id == DataPointSeries.data_source_id)
        .where(
            DataSource.user_id == user_id,
            DataPointSeries.series_type_definition_id == get_series_type_id(series_type),
        )
    ).all()
    return {int(r.recorded_at.timestamp()): (r.value, r.is_daily_total) for r in rows}


class TestDailiesAndFirstEpochAtMidnight:
    """The daily totals and the day's first epoch both start at local midnight.

    Both land on one data source (wellness summaries carry no device), and the series key
    has no is_daily_total, so whichever arrived last used to replace the other: a 15-minute
    count where the day's total belonged, or the slot's count gone.
    """

    def _push_both(self, garmin_247: Garmin247Data, db: Session, order: list[str]) -> UUID:
        user = UserFactory()
        conn = UserConnectionFactory(user=user, provider="garmin")
        items = {"dailies": DAILY, "epochs": FIRST_EPOCH}
        with active_connection(conn.id):
            for kind in order:
                garmin_247.process_items_batch(db, user.id, kind, [items[kind]])
        return user.id

    @pytest.mark.parametrize("order", [["dailies", "epochs"], ["epochs", "dailies"]])
    def test_both_survive_in_either_order(self, garmin_247: Garmin247Data, db: Session, order: list[str]) -> None:
        user_id = self._push_both(garmin_247, db, order)

        assert _rows_with_flag(db, user_id, SeriesType.steps) == {
            DAY_START: (Decimal(9_874), True),
            DAY_START + 1: (Decimal(7), False),
        }
        assert _rows_with_flag(db, user_id, SeriesType.active_energy) == {
            DAY_START: (Decimal(512), True),
            DAY_START + 1: (Decimal(2), False),
        }

    def test_only_the_midnight_slot_moves(self, garmin_247: Garmin247Data) -> None:
        normalized = garmin_247.normalize_epochs([FIRST_EPOCH, SEDENTARY], uuid4())
        samples = garmin_247._build_epochs_samples(uuid4(), normalized)

        at = {(s.series_type, int(s.recorded_at.timestamp())) for s in samples}
        assert (SeriesType.steps, DAY_START + 1) in at
        assert (SeriesType.steps, SLOT) in at
        # Heart rate is not a daily total, and stays on the 15 s all-day grid.
        assert (SeriesType.heart_rate, DAY_START) in at
