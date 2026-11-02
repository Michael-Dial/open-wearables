"""A Whoop notification reaches every profile the Whoop account is connected to."""

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from app.services.providers.whoop.webhook_handler import WhoopWebhookHandler
from app.utils.connection_context import get_active_connection_id


def _connection() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), user_id=uuid4())


def _handler(connections: list[SimpleNamespace]) -> tuple[WhoopWebhookHandler, list[tuple]]:
    seen: list[tuple] = []

    def load_single_sleep(db, user_id, resource_id):  # noqa: ANN001, ANN202
        seen.append((user_id, get_active_connection_id(), resource_id))
        return 1, None

    data_247 = MagicMock()
    data_247.load_single_sleep.side_effect = load_single_sleep
    handler = WhoopWebhookHandler(data_247=data_247, workouts=MagicMock())
    handler.connection_repo = MagicMock()
    handler.connection_repo.get_all_by_provider_user_id.return_value = connections
    return handler, seen


def _payload() -> dict:
    return {"user_id": 10129, "id": "ecfc6a15-4661-442f-a9a4-f160dd7afae8", "type": "sleep.updated", "trace_id": "t"}


def test_sleep_update_is_fetched_once_per_linked_profile_in_its_own_scope() -> None:
    first, second = _connection(), _connection()
    handler, seen = _handler([first, second])

    result = handler.process_payload(MagicMock(), _payload(), trace_id="t")

    assert seen == [
        (first.user_id, first.id, _payload()["id"]),
        (second.user_id, second.id, _payload()["id"]),
    ]
    assert result["status"] == "processed"
    assert result["user_ids"] == [str(first.user_id), str(second.user_id)]
    assert handler.connection_repo.update_last_synced_at.call_count == 2
    assert get_active_connection_id() is None


def test_single_profile_keeps_the_plain_result_shape() -> None:
    only = _connection()
    handler, seen = _handler([only])

    result = handler.process_payload(MagicMock(), _payload(), trace_id="t")

    assert seen == [(only.user_id, only.id, _payload()["id"])]
    assert result == {
        "status": "processed",
        "event_type": "sleep.updated",
        "records_saved": 1,
        "user_id": str(only.user_id),
    }


def test_unknown_whoop_user_is_reported() -> None:
    handler, seen = _handler([])

    result = handler.process_payload(MagicMock(), _payload(), trace_id="t")

    assert seen == []
    assert result["status"] == "user_not_found"
