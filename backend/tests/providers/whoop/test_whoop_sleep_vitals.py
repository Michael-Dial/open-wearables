"""A Whoop night's resting HR and respiratory rate reach the sleep summary, each under its own account.

Whoop states a respiratory rate on every scored sleep (``score.respiratory_rate``) and a resting HR on the
recovery scored on it. The respiratory rate used to be kept only inside the sleep score's components, which no
summary reads, so every Whoop night arrived with none.
"""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from app.models import EventRecordAggregate
from app.schemas.enums import ComputedBy, SeriesType, get_series_type_id
from app.services.providers.whoop.data_247 import Whoop247Data
from app.services.summaries_service import summaries_service
from app.utils.connection_context import active_connection
from tests.factories import UserConnectionFactory, UserFactory

_WINDOW = (datetime(2026, 11, 12, tzinfo=timezone.utc), datetime(2026, 11, 14, tzinfo=timezone.utc))


def _sleep(sleep_id: str, respiratory_rate: float | None) -> dict:
    score: dict = {
        "stage_summary": {
            "total_in_bed_time_milli": 27_180_000,
            "total_awake_time_milli": 1_434_000,
            "total_light_sleep_time_milli": 14_340_000,
            "total_slow_wave_sleep_time_milli": 7_380_000,
            "total_rem_sleep_time_milli": 4_050_000,
        },
        "sleep_performance_percentage": 90,
        "sleep_efficiency_percentage": 95.1,
    }
    if respiratory_rate is not None:
        score["respiratory_rate"] = respiratory_rate
    return {
        "id": sleep_id,
        "cycle_id": 1,
        "start": "2026-11-13T04:25:00.000Z",
        "end": "2026-11-13T11:58:00.000Z",
        "timezone_offset": "-05:00",
        "nap": False,
        "score_state": "SCORED",
        "score": score,
    }


def _recovery(sleep_id: str, rhr: int, hrv: float) -> dict:
    return {
        "cycle_id": 1,
        "sleep_id": sleep_id,
        "created_at": "2026-11-13T12:10:00.000Z",
        "score_state": "SCORED",
        "score": {"recovery_score": 73, "resting_heart_rate": rhr, "hrv_rmssd_milli": hrv},
    }


def _data() -> Whoop247Data:
    return Whoop247Data(provider_name="whoop", api_base_url="https://api.prod.whoop.com", oauth=MagicMock())


def _sync_sleep(db: Session, user_id, connection_id, raw: dict) -> None:  # noqa: ANN001
    data = _data()
    with active_connection(connection_id), patch.object(data, "_fetch_paginated", return_value=([raw], False)):
        data.load_and_save_sleep(db, user_id, *_WINDOW)


def _sync_recovery(db: Session, user_id, connection_id, raw: dict) -> None:  # noqa: ANN001
    data = _data()
    with active_connection(connection_id), patch.object(data, "_fetch_paginated", return_value=([raw], False)):
        data.load_and_save_recovery(db, user_id, *_WINDOW)


def _nights(db: Session, user_id) -> dict:  # noqa: ANN001
    rows = summaries_service.get_sleep_summaries(db, user_id, *_WINDOW, None, 50, filter_by_priority=False).data
    return {r.source.user_connection_id: r for r in rows}


def test_each_account_night_carries_its_own_resting_hr_and_respiratory_rate(db: Session) -> None:
    user = UserFactory()
    five = UserConnectionFactory(user=user, provider="whoop", provider_user_id=None, device_label="WHOOP 5.0")
    four = UserConnectionFactory(user=user, provider="whoop", provider_user_id=None, device_label="WHOOP 4.0")

    _sync_sleep(db, user.id, five.id, _sleep("aaaaaaaa-0000-0000-0000-000000000005", 12.5))
    _sync_sleep(db, user.id, four.id, _sleep("aaaaaaaa-0000-0000-0000-000000000004", 12.1))
    _sync_recovery(db, user.id, five.id, _recovery("aaaaaaaa-0000-0000-0000-000000000005", 47, 78.29))
    _sync_recovery(db, user.id, four.id, _recovery("aaaaaaaa-0000-0000-0000-000000000004", 48, 78.7))

    nights = _nights(db, user.id)
    assert set(nights) == {five.id, four.id}

    assert nights[five.id].avg_respiratory_rate == 12.5
    assert nights[five.id].resting_heart_rate_bpm == 47
    assert nights[four.id].avg_respiratory_rate == 12.1
    assert nights[four.id].resting_heart_rate_bpm == 48
    # Whoop sends no heart-rate samples, so there is no whole-night mean: silence, not a figure borrowed from the RHR.
    assert nights[five.id].avg_heart_rate_bpm is None

    provenance = nights[five.id].vitals_provenance
    assert provenance is not None
    assert provenance.avg_respiratory_rate is not None
    assert provenance.avg_respiratory_rate.computed_by == ComputedBy.PROVIDER
    assert provenance.avg_respiratory_rate.provider is not None
    assert provenance.avg_respiratory_rate.provider.scope == "sleep"


def test_a_sleep_without_a_respiratory_rate_reports_none(db: Session) -> None:
    user = UserFactory()
    five = UserConnectionFactory(user=user, provider="whoop", provider_user_id=None, device_label="WHOOP 5.0")

    _sync_sleep(db, user.id, five.id, _sleep("aaaaaaaa-0000-0000-0000-000000000005", None))

    (night,) = _nights(db, user.id).values()
    assert night.avg_respiratory_rate is None


def test_resync_keeps_one_figure_per_sleep(db: Session) -> None:
    user = UserFactory()
    five = UserConnectionFactory(user=user, provider="whoop", provider_user_id=None, device_label="WHOOP 5.0")

    _sync_sleep(db, user.id, five.id, _sleep("aaaaaaaa-0000-0000-0000-000000000005", 12.5))
    _sync_sleep(db, user.id, five.id, _sleep("aaaaaaaa-0000-0000-0000-000000000005", 12.5))

    rows = (
        db.query(EventRecordAggregate)
        .filter(
            EventRecordAggregate.series_type_definition_id == get_series_type_id(SeriesType.respiratory_rate),
            EventRecordAggregate.computed_by == ComputedBy.PROVIDER,
        )
        .all()
    )
    assert len(rows) == 1
    assert rows[0].avg == Decimal("12.5")
