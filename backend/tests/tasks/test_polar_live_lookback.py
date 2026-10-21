"""A live Polar pull re-reads the last few days, because AccessLink data arrives late.

AccessLink files everything under the day it was recorded, and it reaches Polar only
when the watch syncs with Flow, hours or days later. A live pull that started at the
last sync asked only for the days since then, so a night or a day of continuous HR that
was uploaded after its day had passed was never requested, and Nightly Recharge's
5-minute HRV for that night was dropped for want of the sleep record that places it.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.integrations.celery.tasks.sync_vendor_data_task import live_pull_lookback, sync_vendor_data
from app.schemas.auth import ConnectionStatus
from app.services.providers.polar.strategy import PolarStrategy
from tests.factories import UserConnectionFactory, UserFactory


class TestLivePullLookback:
    def test_the_longer_window_wins(self) -> None:
        assert live_pull_lookback(timedelta(hours=2), timedelta(days=3)) == timedelta(days=3)
        assert live_pull_lookback(timedelta(days=5), timedelta(days=3)) == timedelta(days=5)

    def test_either_alone_applies(self) -> None:
        assert live_pull_lookback(None, timedelta(days=3)) == timedelta(days=3)
        assert live_pull_lookback(timedelta(hours=2), None) == timedelta(hours=2)

    def test_none_or_zero_means_no_lookback(self) -> None:
        assert live_pull_lookback(None, None) is None
        assert live_pull_lookback(timedelta(0), None) is None

    def test_polar_sets_a_floor(self) -> None:
        assert PolarStrategy().capabilities.live_pull_lookback == timedelta(days=3)


def _night(day: date) -> dict[str, Any]:
    before = day - timedelta(days=1)
    return {
        "polar_user": "https://www.polaraccesslink.com/v3/users/123",
        "date": day.isoformat(),
        "sleep_start_time": f"{before.isoformat()}T23:00:00-04:00",
        "sleep_end_time": f"{day.isoformat()}T07:00:00-04:00",
        "device_id": "Polar Vantage V3",
        "light_sleep": 14400,
        "deep_sleep": 5400,
        "rem_sleep": 7200,
        "total_interruption_duration": 1800,
        "sleep_score": 82,
        "hypnogram": {"23:00": 0, "23:30": 2, "00:30": 4, "02:30": 1, "04:00": 2},
        "heart_rate_samples": {"23:30": 58, "00:00": 54},
    }


def _recharge(day: date) -> dict[str, Any]:
    return {
        "polar_user": "https://www.polaraccesslink.com/v3/users/123",
        "date": day.isoformat(),
        "nightly_recharge_status": 4,
        "heart_rate_variability_avg": 60,
        "hrv_samples": {"23:40": 55, "23:45": 61},
    }


class TestLateUploadedNight:
    @patch("app.integrations.celery.tasks.sync_vendor_data_task.SessionLocal")
    def test_a_night_uploaded_after_its_day_is_still_fetched_once(
        self, mock_session_local: MagicMock, db: Session, mock_celery_tasks: MagicMock
    ) -> None:
        now = datetime.now(timezone.utc)
        night = (now - timedelta(days=2)).date()
        user = UserFactory()
        UserConnectionFactory(
            user=user,
            provider="polar",
            provider_user_id="123",
            status=ConnectionStatus.ACTIVE,
            refresh_token=None,
            token_expires_at=now + timedelta(days=3650),
            # The hourly pull ran an hour ago: the night's day had long passed by then.
            last_synced_at=now - timedelta(hours=1),
        )
        db.commit()
        mock_session_local.return_value.__enter__.return_value = db
        mock_session_local.return_value.__exit__.return_value = None
        activity_windows: list[tuple[str, str]] = []

        def polar(self: httpx.Client, method: str, url: str, params: dict | None = None, **_: Any) -> httpx.Response:
            path = httpx.URL(url).path
            request = httpx.Request(method, url)
            if path == "/v3/users/sleep/available":
                return httpx.Response(200, json={"available": [{"date": night.isoformat()}]}, request=request)
            if path == f"/v3/users/sleep/{night.isoformat()}":
                return httpx.Response(200, json=_night(night), request=request)
            if path == "/v3/users/nightly-recharge":
                return httpx.Response(200, json={"recharges": [_recharge(night)]}, request=request)
            if path == "/v3/users/activities":
                activity_windows.append(((params or {})["from"], (params or {})["to"]))
            return httpx.Response(204, request=request)

        with patch.object(httpx.Client, "request", polar):
            sync_vendor_data(str(user.id))
            sync_vendor_data(str(user.id))

        assert activity_windows[0][0] <= night.isoformat()
        sleeps = db.execute(
            text(
                "select count(*) from event_record e join data_source d on d.id = e.data_source_id "
                "where d.user_id = :u and e.category = 'sleep'"
            ),
            {"u": user.id},
        ).scalar()
        hrv = db.execute(
            text(
                "select count(*) from data_point_series s join data_source d on d.id = s.data_source_id "
                "join series_type_definition t on t.id = s.series_type_definition_id "
                "where d.user_id = :u and t.code = 'heart_rate_variability_rmssd'"
            ),
            {"u": user.id},
        ).scalar()
        assert sleeps == 1
        assert hrv == 2


class TestNoExercises:
    def test_an_empty_exercise_list_is_no_exercises_not_a_failure(self, db: Session) -> None:
        user = UserFactory()
        UserConnectionFactory(
            user=user,
            provider="polar",
            provider_user_id="123",
            refresh_token=None,
            token_expires_at=datetime.now(timezone.utc) + timedelta(days=3650),
        )
        db.commit()

        def no_content(self: httpx.Client, method: str, url: str, **_: Any) -> httpx.Response:
            return httpx.Response(204, request=httpx.Request(method, url))

        with patch.object(httpx.Client, "request", no_content):
            assert PolarStrategy().workouts.load_data(db, user.id) == 0
