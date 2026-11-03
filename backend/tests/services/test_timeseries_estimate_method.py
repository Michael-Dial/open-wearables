"""A raw /timeseries row states which estimate it is when its reader recorded one."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.schemas.enums.series_types import SeriesType, get_series_type_id
from app.services.timeseries_service import _to_sample

AT = datetime(2026, 10, 30, 7, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"estimate_method": "run-vo2-max"}, "run-vo2-max"),
        ({"HKVO2MaxTestType": "2"}, None),  # stored raw, not typed yet
        ({"estimate_method": ""}, None),
        ({"estimate_method": 3}, None),
        (None, None),
    ],
)
def test_estimate_method_is_read_verbatim_or_left_unstated(metadata: dict | None, expected: str | None) -> None:
    sample = _to_sample(AT, None, get_series_type_id(SeriesType.vo2_max), Decimal("47"), None, None, metadata)

    assert sample.estimate_method == expected
