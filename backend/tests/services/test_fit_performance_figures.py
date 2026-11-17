"""A FIT file's one-per-workout figures: Training Effect, training load, and the thresholds in effect."""

from decimal import Decimal
from uuid import uuid4

from app.schemas.enums.series_types import SeriesType
from app.services.fit_parser import parse_fit_file
from tests.fixtures.fit_builder import make_performance_fit, make_running_fit


def _parse(**kwargs: object) -> object:
    return parse_fit_file(make_performance_fit(**kwargs), uuid4(), source="garmin")


class TestSessionFigures:
    def test_training_effect_load_and_thresholds_of_a_single_session(self) -> None:
        result = _parse()

        assert result.session_metrics == {
            SeriesType.aerobic_training_effect: Decimal("3.4"),
            SeriesType.anaerobic_training_effect: Decimal("1.2"),
            SeriesType.training_load_peak: Decimal("142.0"),
            SeriesType.threshold_heart_rate: Decimal("168"),
            SeriesType.functional_threshold_power: Decimal("265"),
        }

    def test_the_session_s_own_zones_win_over_the_file_wide_target(self) -> None:
        """time_in_zone holds the sport's thresholds; zones_target is the fallback."""
        assert _parse().session_metrics[SeriesType.threshold_heart_rate] == Decimal("168")

    def test_without_time_in_zone_the_file_wide_target_is_read(self) -> None:
        result = _parse(time_in_zone=False)

        assert result.session_metrics[SeriesType.threshold_heart_rate] == Decimal("160")
        assert result.session_metrics[SeriesType.functional_threshold_power] == Decimal("250")
        assert result.hr_zones is None

    def test_an_unscored_session_writes_no_training_effect(self) -> None:
        """A Training Effect of 0 is a session the device did not score, not a score of zero."""
        result = _parse(aerobic_te=0, anaerobic_te=0)

        assert SeriesType.aerobic_training_effect not in result.session_metrics
        assert SeriesType.anaerobic_training_effect not in result.session_metrics
        assert SeriesType.training_load_peak in result.session_metrics

    def test_a_multisport_file_gives_no_per_workout_figures(self) -> None:
        """Each session has its own; the workout record is one of them or none, so none is taken."""
        result = _parse(sessions=2)

        assert result.session_count == 2
        assert result.session_metrics == {}

    def test_a_file_without_these_messages_gives_none(self) -> None:
        result = parse_fit_file(make_running_fit(), uuid4(), source="garmin")

        assert result.session_metrics == {}
        assert result.undocumented_messages == {}


class TestZoneBasis:
    def test_zones_say_what_they_are_a_percentage_of(self) -> None:
        result = _parse()

        assert result.hr_zones is not None
        assert result.hr_zones.calc_type == "percent_lthr"
        assert result.hr_zones.threshold_hr == 168
        assert result.hr_zones.resting_hr == 48
        assert result.hr_zones.max_hr == 191
        assert result.power_zones is not None
        assert result.power_zones.calc_type == "percent_ftp"
        assert result.power_zones.ftp_watts == 265


class TestUndocumentedMessages:
    def test_an_undocumented_message_is_inventoried_not_decoded(self) -> None:
        result = _parse()

        assert result.undocumented_messages == {140: [4, 14]}
        assert SeriesType.vo2_max not in result.session_metrics
