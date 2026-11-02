"""One Whoop sleep fetched through two profiles must not share an event_record primary key."""

from unittest.mock import MagicMock
from uuid import UUID, uuid4

from app.services.providers.whoop.data_247 import Whoop247Data

WHOOP_SLEEP_ID = "ecfc6a15-4661-442f-a9a4-f160dd7afae8"


def _raw(sleep_id: str | None = WHOOP_SLEEP_ID) -> dict:
    return {
        "id": sleep_id,
        "start": "2026-11-02T05:03:00.000Z",
        "end": "2026-11-02T12:38:00.000Z",
        "nap": False,
        "score_state": "SCORED",
        "score": {"stage_summary": {"total_in_bed_time_milli": 27_300_000}},
    }


def _data() -> Whoop247Data:
    return Whoop247Data(provider_name="whoop", api_base_url="https://api.prod.whoop.com", oauth=MagicMock())


def test_same_whoop_sleep_gets_a_distinct_record_id_per_user() -> None:
    first, _ = _data().normalize_sleep(_raw(), uuid4())
    second, _ = _data().normalize_sleep(_raw(), uuid4())

    assert first["id"] != second["id"]
    assert first["id"] != UUID(WHOOP_SLEEP_ID)
    # The Whoop id still travels as the external id, which is what re-ingestion matches on.
    assert first["whoop_sleep_id"] == second["whoop_sleep_id"] == WHOOP_SLEEP_ID


def test_record_id_is_stable_for_one_user_across_syncs() -> None:
    user_id = uuid4()

    first, _ = _data().normalize_sleep(_raw(), user_id)
    again, _ = _data().normalize_sleep(_raw(), user_id)

    assert first["id"] == again["id"]


def test_sleep_without_a_whoop_id_still_gets_an_id() -> None:
    normalized, _ = _data().normalize_sleep(_raw(None), uuid4())

    assert isinstance(normalized["id"], UUID)
