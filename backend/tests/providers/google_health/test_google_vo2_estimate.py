"""Google's two VO2max data types land in one series and say which one they are.

run-vo2-max (Fitbit's running estimate) and vo2-max (the generic one) are different
algorithms that both map to vo2_max on the same data source. Without a label on the
row, a device-vs-device comparison pools them and compares neither.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.schemas.enums import SeriesType
from app.schemas.providers.google import DataTypeMetric
from app.services.providers.google_health.data_247 import GoogleHealth247Data
from app.services.providers.google_health.metrics import METRICS

USER_ID = uuid4()
WINDOW = (datetime(2026, 5, 1, tzinfo=timezone.utc), datetime(2026, 5, 3, tzinfo=timezone.utc))
SAMPLE_TIME = {"physicalTime": "2026-05-02T07:00:00Z", "utcOffset": "-14400s"}


@pytest.fixture
def data_247() -> GoogleHealth247Data:
    return GoogleHealth247Data(oauth=MagicMock(), connection_repo=MagicMock(), api_base_url="https://x")


def _metric(data_type: str) -> DataTypeMetric:
    return next(m for m in METRICS if m.data_type == data_type)


@pytest.mark.parametrize(
    ("data_type", "point"),
    [
        ("run-vo2-max", {"runVo2Max": {"sampleTime": SAMPLE_TIME, "runVo2Max": 47.3}}),
        ("vo2-max", {"vo2Max": {"sampleTime": SAMPLE_TIME, "vo2Max": 44.0}}),
    ],
)
def test_each_vo2max_row_names_the_data_type_that_wrote_it(
    data_247: GoogleHealth247Data, data_type: str, point: dict
) -> None:
    with patch.object(data_247, "_fetch_points", return_value=iter([point])):
        samples = data_247._native_samples(MagicMock(), USER_ID, _metric(data_type), *WINDOW)

    assert [(s.series_type, s.provider_metadata) for s in samples] == [
        (SeriesType.vo2_max, {"estimate_method": data_type})
    ]


def test_other_series_carry_no_estimate_label(data_247: GoogleHealth247Data) -> None:
    point = {"heartRate": {"sampleTime": SAMPLE_TIME, "beatsPerMinute": 58}}

    with patch.object(data_247, "_fetch_points", return_value=iter([point])):
        samples = data_247._native_samples(MagicMock(), USER_ID, _metric("heart-rate"), *WINDOW)

    assert len(samples) == 1
    assert samples[0].provider_metadata is None
