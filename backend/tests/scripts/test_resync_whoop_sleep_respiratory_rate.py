"""Every active Whoop account that holds sleep is re-pulled from its own earliest night, under its own account."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from app.schemas.auth.connection_status import ConnectionStatus
from scripts.data_migrations.resync_whoop_sleep_respiratory_rate import queue_resyncs, whoop_resyncs
from tests.factories import DataSourceFactory, EventRecordFactory, UserConnectionFactory, UserFactory

_FIRST = datetime(2026, 8, 3, 4, 0, tzinfo=timezone.utc)


def _sleep(user, connection, start: datetime) -> None:  # noqa: ANN001
    source = DataSourceFactory(
        user=user, provider=connection.provider, source=connection.provider, user_connection_id=connection.id
    )
    EventRecordFactory(
        data_source=source, category="sleep", start_datetime=start, end_datetime=start + timedelta(hours=7)
    )


def test_each_whoop_account_is_re_pulled_from_its_earliest_sleep(db: Session) -> None:
    user = UserFactory()
    five = UserConnectionFactory(user=user, provider="whoop", provider_user_id=None)
    four = UserConnectionFactory(user=user, provider="whoop", provider_user_id=None)
    _sleep(user, five, _FIRST)
    _sleep(user, five, _FIRST + timedelta(days=30))
    _sleep(user, four, _FIRST + timedelta(days=10))

    plan = {connection: (user_id, start) for connection, user_id, start in whoop_resyncs(db)}

    assert plan == {
        five.id: (user.id, _FIRST - timedelta(days=1)),
        four.id: (user.id, _FIRST + timedelta(days=9)),
    }


def test_accounts_without_sleep_inactive_or_other_providers_are_left_alone(db: Session) -> None:
    user = UserFactory()
    UserConnectionFactory(user=user, provider="whoop", provider_user_id=None)
    revoked = UserConnectionFactory(user=user, provider="whoop", provider_user_id=None, status=ConnectionStatus.REVOKED)
    _sleep(user, revoked, _FIRST)
    oura = UserConnectionFactory(user=user, provider="oura", provider_user_id=None)
    _sleep(user, oura, _FIRST)

    assert whoop_resyncs(db) == []


def test_queues_one_historical_whoop_sync_per_account(db: Session) -> None:
    user = UserFactory()
    five = UserConnectionFactory(user=user, provider="whoop", provider_user_id=None)
    _sleep(user, five, _FIRST)

    delay = MagicMock()
    with patch("app.integrations.celery.tasks.sync_vendor_data_task.sync_vendor_data.delay", delay):
        assert queue_resyncs(db, dry_run=False)

    delay.assert_called_once_with(
        user_id=str(user.id),
        start_date=(_FIRST - timedelta(days=1)).isoformat(),
        providers=["whoop"],
        is_historical=True,
        connection_ids=[str(five.id)],
    )


def test_dry_run_queues_nothing(db: Session) -> None:
    user = UserFactory()
    five = UserConnectionFactory(user=user, provider="whoop", provider_user_id=None)
    _sleep(user, five, _FIRST)

    delay = MagicMock()
    with patch("app.integrations.celery.tasks.sync_vendor_data_task.sync_vendor_data.delay", delay):
        assert not queue_resyncs(db, dry_run=True)

    delay.assert_not_called()
