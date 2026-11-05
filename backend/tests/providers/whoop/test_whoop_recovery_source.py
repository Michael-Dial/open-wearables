"""Two Whoop accounts on one profile: each account's recovery is reported under its own source."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from app.services.providers.whoop.data_247 import Whoop247Data
from app.services.summaries_service import summaries_service
from app.utils.connection_context import active_connection
from tests.factories import UserConnectionFactory, UserFactory


def _recovery(hrv: float, score: int) -> dict:
    return {
        "cycle_id": 93845,
        "sleep_id": "ecfc6a15-4661-442f-a9a4-f160dd7afae8",
        "created_at": "2026-11-05T11:30:00.000Z",
        "score_state": "SCORED",
        "score": {"recovery_score": score, "resting_heart_rate": 52, "hrv_rmssd_milli": hrv, "spo2_percentage": 95.0},
    }


def _sync(db: Session, user_id, connection_id, raw: dict) -> None:  # noqa: ANN001
    data = Whoop247Data(provider_name="whoop", api_base_url="https://api.prod.whoop.com", oauth=MagicMock())
    with active_connection(connection_id), patch.object(data, "_fetch_paginated", return_value=([raw], False)):
        data.load_and_save_recovery(
            db, user_id, datetime(2026, 11, 4, tzinfo=timezone.utc), datetime(2026, 11, 6, tzinfo=timezone.utc)
        )


def test_each_account_recovery_names_its_own_account(db: Session) -> None:
    user = UserFactory()
    five = UserConnectionFactory(user=user, provider="whoop", provider_user_id=None, device_label="WHOOP 5.0")
    four = UserConnectionFactory(user=user, provider="whoop", provider_user_id=None, device_label="WHOOP 4.0")

    _sync(db, user.id, five.id, _recovery(hrv=65.3, score=50))
    _sync(db, user.id, four.id, _recovery(hrv=67.4, score=48))

    rows = summaries_service.get_recovery_summaries(
        db,
        user.id,
        datetime(2026, 11, 4, tzinfo=timezone.utc),
        datetime(2026, 11, 6, tzinfo=timezone.utc),
        None,
        50,
        filter_by_priority=False,
    ).data

    by_account = {r.source.user_connection_id: r for r in rows}
    assert set(by_account) == {five.id, four.id}
    assert by_account[five.id].source.device == "WHOOP 5.0"
    assert by_account[five.id].recovery_score == 50
    assert by_account[four.id].source.device == "WHOOP 4.0"
    assert by_account[four.id].recovery_score == 48


def test_resync_replaces_the_score_stored_without_a_source(db: Session) -> None:
    from uuid import uuid4

    from app.models import HealthScore
    from app.schemas.enums import HealthScoreCategory, ProviderName

    user = UserFactory()
    five = UserConnectionFactory(user=user, provider="whoop", provider_user_id=None, device_label="WHOOP 5.0")
    db.add(
        HealthScore(
            id=uuid4(),
            user_id=user.id,
            provider=ProviderName.WHOOP,
            category=HealthScoreCategory.RECOVERY,
            value=50,
            recorded_at=datetime(2026, 11, 5, 11, 30, tzinfo=timezone.utc),
            data_source_id=None,
        )
    )
    db.flush()

    _sync(db, user.id, five.id, _recovery(hrv=65.3, score=50))

    scores = db.query(HealthScore).filter(HealthScore.user_id == user.id).all()
    assert len(scores) == 1
    assert scores[0].data_source_id is not None
