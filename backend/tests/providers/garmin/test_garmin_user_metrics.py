"""Garmin userMetrics: the running and cycling VO2max are two estimates, kept apart."""

from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.schemas.enums.series_types import SeriesType
from app.services.providers.garmin.data_247 import Garmin247Data


@pytest.fixture
def garmin_247() -> Garmin247Data:
    return Garmin247Data(provider_name="garmin", api_base_url="https://apis.garmin.com", oauth=MagicMock())


def _by_type(samples: list) -> dict[SeriesType, Decimal]:
    return {s.series_type: s.value for s in samples}


def test_running_and_cycling_vo2max_land_in_separate_series(garmin_247: Garmin247Data) -> None:
    metrics = {
        "summaryId": "um_1",
        "calendarDate": "2026-10-30",
        "vo2Max": 52.0,
        "vo2MaxCycling": 49.0,
        "fitnessAge": 31,
    }

    samples = garmin_247._build_user_metrics_samples(uuid4(), metrics)

    assert _by_type(samples) == {
        SeriesType.vo2_max: Decimal("52.0"),
        SeriesType.garmin_vo2_max_cycling: Decimal("49.0"),
        SeriesType.garmin_fitness_age: Decimal("31"),
    }
    # Same day, same summary: only the series keeps them from overwriting each other.
    assert len({s.recorded_at for s in samples}) == 1


@pytest.mark.parametrize("absent", [{}, {"vo2MaxCycling": None}])
def test_a_day_without_a_cycling_estimate_writes_no_cycling_row(garmin_247: Garmin247Data, absent: dict) -> None:
    metrics = {"summaryId": "um_2", "calendarDate": "2026-10-30", "vo2Max": 52.0, **absent}

    samples = garmin_247._build_user_metrics_samples(uuid4(), metrics)

    assert SeriesType.garmin_vo2_max_cycling not in _by_type(samples)
