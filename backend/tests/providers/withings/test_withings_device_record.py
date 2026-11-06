"""Which Withings device took each reading, dated by the readings.

Every Withings measure lands on one data source, so a scale and a blood-pressure cuff
on one account share it. The measure group's ``deviceid`` and ``model`` say which took
a reading; activity rows carry a ``deviceid``; sleep summaries carry the model. All were
parsed and dropped. These pin that each becomes a dated source observation.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy.orm import Session

from app.models import DataSourceObservation, User
from app.services.providers.withings.data_247 import Withings247Data, _SleepSeries
from tests.factories import UserFactory

WINDOW = (datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 10, 31, tzinfo=timezone.utc))


def _data_247() -> Withings247Data:
    data_247 = Withings247Data(provider_name="withings", api_base_url="https://wbsapi.withings.net", oauth=MagicMock())
    data_247._active_connection_id = MagicMock(return_value=None)  # type: ignore[method-assign]
    return data_247


def _group(epoch: int, deviceid: str | None, model: str | None) -> dict:
    return {
        "date": epoch,
        "grpid": epoch,
        "attrib": 0,
        "category": 1,
        "deviceid": deviceid,
        "model": model,
        "measures": [{"value": 7012, "type": 1, "unit": -2}],
    }


def _observations(db: Session, user: User) -> list[DataSourceObservation]:
    return (
        db.query(DataSourceObservation)
        .filter(DataSourceObservation.user_id == user.id)
        .order_by(DataSourceObservation.first_recorded_at)
        .all()
    )


@patch("app.services.providers.withings.data_247.paginate")
def test_a_scale_and_a_cuff_on_one_account_are_told_apart(mock_page: MagicMock, db: Session) -> None:
    user = UserFactory()
    mock_page.return_value = MagicMock(
        rows=[
            _group(1_790_000_000, "scale-hash", "Body+"),
            _group(1_790_086_400, "scale-hash", "Body+"),
            _group(1_790_100_000, "cuff-hash", "BPM Connect"),
        ],
        envelope={"timezone": "UTC"},
    )

    _data_247().save_measures(db, user.id, *WINDOW)

    scale, cuff = _observations(db, user)
    assert (scale.device_local_identifier, scale.device_model) == ("scale-hash", "Body+")
    assert (cuff.device_local_identifier, cuff.device_model) == ("cuff-hash", "BPM Connect")
    assert scale.data_source_id == cuff.data_source_id
    assert scale.records_reported == 2
    assert scale.first_recorded_at == datetime.fromtimestamp(1_790_000_000, tz=timezone.utc)
    assert scale.last_recorded_at == datetime.fromtimestamp(1_790_086_400, tz=timezone.utc)


@patch("app.services.providers.withings.data_247.paginate")
def test_a_group_naming_no_device_records_nothing(mock_page: MagicMock, db: Session) -> None:
    user = UserFactory()
    mock_page.return_value = MagicMock(rows=[_group(1_790_000_000, None, None)], envelope={})

    _data_247().save_measures(db, user.id, *WINDOW)

    assert _observations(db, user) == []


def test_activity_rows_name_their_tracker(db: Session) -> None:
    user = UserFactory()
    rows = [{"date": "2026-10-05", "timezone": "UTC", "brand": 1, "deviceid": "tracker-hash", "steps": 9000}]

    _data_247().save_activity(db, user.id, *WINDOW, rows=rows)

    [obs] = _observations(db, user)
    assert obs.device_local_identifier == "tracker-hash"


def test_a_night_records_its_model_verbatim(db: Session) -> None:
    user = UserFactory()
    data_247 = _data_247()
    data_247._fetch_sleep_series = MagicMock(return_value=_SleepSeries([], []))  # type: ignore[method-assign]
    night = {
        "startdate": 1_790_000_000,
        "enddate": 1_790_028_800,
        "id": 77,
        "timezone": "UTC",
        "model": 32,
        "model_id": 63,
        "data": {"total_timeinbed": 28800, "total_sleep_time": 25200},
    }
    with patch("app.services.providers.withings.data_247.paginate") as mock_page:
        mock_page.return_value = MagicMock(rows=[night])
        data_247.save_sleep(db, user.id, *WINDOW)

    [obs] = _observations(db, user)
    assert (obs.device_model, obs.device_type_reported) == ("63", "32")
    assert obs.device_local_identifier is None
