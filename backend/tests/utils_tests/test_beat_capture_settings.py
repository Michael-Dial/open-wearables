"""Beat-capture defaults are bounded like the columns they fill in for."""

import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.mark.parametrize(
    "overrides",
    [
        {"beat_capture_default_lookback_days": 0},
        {"beat_capture_default_lookback_days": 60},
        {"beat_capture_default_sleep_padding_minutes": -1},
        {"beat_capture_default_sleep_padding_minutes": 241},
        {"beat_capture_max_window_days": 0},
        {"beat_capture_default_mode": "sometimes"},
    ],
)
def test_out_of_range_default_fails_at_startup(overrides: dict) -> None:
    with pytest.raises(ValidationError):
        Settings(**overrides)


def test_defaults_are_valid() -> None:
    settings = Settings()

    assert settings.beat_capture_default_mode == "sleep"
    assert settings.beat_capture_default_lookback_days == 7
    assert settings.beat_capture_default_sleep_padding_minutes == 30
    assert settings.beat_capture_max_window_days == 7
