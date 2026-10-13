"""Apple Watch beat-to-beat rows reach /timeseries with their series and gap flag.

The iOS SDK uploads one row per beat of an HKHeartbeatSeriesSample into
``pulse_to_pulse_interval``: the interval since the previous beat at the beat's own time.
A validation study aligning these against a reference needs the millisecond timestamps,
the series each beat belongs to, and HealthKit's ``precededByGap`` flag to know which
values span a gap rather than measure a beat.
"""

import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import User
from app.services.sdk.import_service import ImportService

WATCH = {"name": "MD Apple Watch Ultra 4", "bundleIdentifier": "com.apple.health.test", "productType": "Watch8,1"}
SERIES = "6F4E2B1A-3C5D-4E7F-8A9B-0C1D2E3F4A5B"


@pytest.fixture(autouse=True)
def mock_sleep_redis() -> Any:
    with (
        patch("app.services.sdk.sleep_service.get_redis_client") as mock_get_redis,
        patch("app.integrations.celery.tasks.finalize_stale_sleep_task.finalize_stale_sleeps"),
    ):
        mock_get_redis.return_value = MagicMock()
        yield


def _beat(index: int, at: str, interval_ms: float, gap: bool = False) -> dict[str, Any]:
    return {
        "id": f"{SERIES}:{index}",
        "type": "HKDataTypeIdentifierHeartbeatSeries",
        "value": interval_ms,
        "unit": "ms",
        "startDate": at,
        "endDate": at,
        "source": WATCH,
        "metadata": {"seriesId": SERIES, "beatIndex": index, "precededByGap": gap},
    }


def _save(db: Session, user: User) -> None:
    records = [
        _beat(1, "2026-10-12T07:14:15.050Z", 840.0),
        _beat(2, "2026-10-12T07:14:15.880Z", 830.0),
        _beat(3, "2026-10-12T07:14:22.400Z", 6520.0, gap=True),
        # An ordinary HRV value in the same span carries no beat fields.
        {
            "id": "sdnn-1",
            "type": "HKQuantityTypeIdentifierHeartRateVariabilitySDNN",
            "value": 51.5,
            "unit": "ms",
            "startDate": "2026-10-12T07:14:00Z",
            "endDate": "2026-10-12T07:19:00Z",
            "source": WATCH,
        },
    ]
    payload = {
        "provider": "apple",
        "sdkVersion": "0.15.0",
        "syncTimestamp": "2026-10-12T08:00:00Z",
        "data": {"records": records},
    }
    ImportService(log=logging.getLogger("test")).load_data(db, payload, str(user.id))


def _get(client: TestClient, user: User, headers: dict[str, str], types: str) -> list[dict]:
    response = client.get(
        f"/api/v1/users/{user.id}/timeseries?types={types}"
        "&start_time=2026-10-12T07:00:00Z&end_time=2026-10-12T08:00:00Z&resolution=raw&limit=100",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


class TestAppleBeatToBeat:
    def test_beats_come_back_with_millisecond_times_series_and_gap_flag(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        _save(db, user)

        rows = _get(client, user, api_key_header, "pulse_to_pulse_interval")

        assert [(r["timestamp"][:23], r["value"], r["beat_series_id"], r["preceded_by_gap"]) for r in rows] == [
            ("2026-10-12T07:14:15.050", 840.0, SERIES, False),
            ("2026-10-12T07:14:15.880", 830.0, SERIES, False),
            ("2026-10-12T07:14:22.400", 6520.0, SERIES, True),
        ]
        assert {r["unit"] for r in rows} == {"ms"}

    def test_other_series_carry_no_beat_fields(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        _save(db, user)

        rows = _get(client, user, api_key_header, "heart_rate_variability_sdnn")

        assert [(r["beat_series_id"], r["preceded_by_gap"]) for r in rows] == [(None, None)]
