"""Tests for SummariesService."""

import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.integrations import rollup_queue
from app.integrations.rollup_queue import requeue_dirty_days
from app.models import DataSource, EventRecord, EventRecordAggregate, ProviderPriority, SeriesAggregate
from app.models.archival_setting import ArchivalSetting
from app.repositories import AggregateRepository, EventRecordRepository, ProviderPriorityRepository
from app.repositories.data_migration_repository import DataMigrationRepository
from app.schemas.enums import ComputedBy, HealthScoreCategory, ProviderName, SeriesType
from app.schemas.model_crud.activities import CalendarPeriod
from app.services.summaries_service import SummariesService
from app.utils.exceptions import InvalidCursorError
from tests.factories import (
    DataPointSeriesFactory,
    DataSourceFactory,
    EventRecordFactory,
    HealthScoreFactory,
    PersonalRecordFactory,
    SeriesTypeDefinitionFactory,
    SleepDetailsFactory,
    UserFactory,
    WorkoutDetailsFactory,
    provider_day,
    roll_up_everything,
)


@pytest.fixture
def service() -> SummariesService:
    return SummariesService()


def _dt(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def _rollup_reached_the_history(db: Session) -> None:
    db.merge(ArchivalSetting(id=1, rollup_enabled=True, delete_after_days=None))
    DataMigrationRepository().mark_completed(db, "aggregate_backfill:rollup_all")
    db.flush()


# ---------------------------------------------------------------------------
# _filter_by_priority
# ---------------------------------------------------------------------------


class TestFilterByPriority:
    def test_returns_empty_for_empty_input(self, db: Session, service: SummariesService) -> None:
        result = service._filter_by_priority(db, uuid4(), [])
        assert result == []

    def test_single_entry_passes_through(self, db: Session, service: SummariesService) -> None:
        entry = {"activity_date": date(2026, 1, 1), "source": "garmin", "device_model": None}
        result = service._filter_by_priority(db, uuid4(), [entry])
        assert result == [entry]

    def test_picks_one_entry_per_date(self, db: Session, service: SummariesService) -> None:
        entries = [
            {"activity_date": date(2026, 1, 1), "source": "garmin", "device_model": None},
            {"activity_date": date(2026, 1, 1), "source": "apple_health_sdk", "device_model": None},
            {"activity_date": date(2026, 1, 2), "source": "garmin", "device_model": None},
        ]
        result = service._filter_by_priority(db, uuid4(), entries)
        assert len(result) == 2
        dates = {r["activity_date"] for r in result}
        assert dates == {date(2026, 1, 1), date(2026, 1, 2)}

    def test_prefers_stored_device_type_over_the_model_string(self, db: Session, service: SummariesService) -> None:
        """A watch without a hardware model must still outrank the phone.

        HealthKit identifies some sources by name only, so the watch row carries
        device_model=None and is rankable only through the device_type resolved at
        ingest. Ignoring that column left the watch at the 99 sentinel and dropped
        it from every day the phone also reported.
        """
        watch = {
            "activity_date": date(2026, 1, 1),
            "provider": "apple",
            "source": "Ali's Apple Watch",
            "device_model": None,
            "device_type": "watch",
        }
        phone = {
            "activity_date": date(2026, 1, 1),
            "provider": "apple",
            "source": "Ali's iPhone",
            "device_model": "iPhone17,1",
            "device_type": "phone",
        }
        result = service._filter_by_priority(db, uuid4(), [phone, watch])
        assert result == [watch]

    def test_falls_back_to_the_model_when_device_type_is_absent(self, db: Session, service: SummariesService) -> None:
        """Legacy rows stored before device_type was populated still rank correctly."""
        watch = {
            "activity_date": date(2026, 1, 1),
            "provider": "apple",
            "source": "apple",
            "device_model": "Watch7,5",
        }
        phone = {
            "activity_date": date(2026, 1, 1),
            "provider": "apple",
            "source": "apple",
            "device_model": "iPhone17,1",
        }
        result = service._filter_by_priority(db, uuid4(), [phone, watch])
        assert result == [watch]

    def test_prefers_the_row_that_measured_something_when_priorities_tie(
        self, db: Session, service: SummariesService
    ) -> None:
        """Two rows for one device on one day must not be resolved by luck.

        Apple splits a watch across a "…Apple Watch" and a "…Apple Watch Ultra 3"
        source. Both carry the same provider and device type, so the old key was
        identical and the winner fell out of Postgres's arbitrary group order — a
        row of zeroes could take the day and drop every other source with it.
        """
        empty = {
            "activity_date": date(2026, 1, 10),
            "provider": "apple",
            "source": "Michael's Apple Watch",
            "device_model": "iPhone18,1",
            "device_type": "watch",
            "steps_sum": 0,
            "active_energy_sum": 0,
            "hr_avg": None,
        }
        measured = {
            "activity_date": date(2026, 1, 10),
            "provider": "apple",
            "source": "Michael's Apple Watch Ultra 3",
            "device_model": "iPhone18,1",
            "device_type": "watch",
            "steps_sum": 7831,
            "active_energy_sum": 571.275,
            "hr_avg": 156,
        }
        # Winner must not depend on the order the rows arrive in.
        assert service._filter_by_priority(db, uuid4(), [empty, measured]) == [measured]
        assert service._filter_by_priority(db, uuid4(), [measured, empty]) == [measured]

    def test_configured_priority_still_outranks_a_richer_row(self, db: Session, service: SummariesService) -> None:
        """The tie-break is a tie-break — it never overrides a device-type priority."""
        watch = {
            "activity_date": date(2026, 1, 10),
            "provider": "apple",
            "source": "Michael's Apple Watch",
            "device_model": "Watch7,12",
            "device_type": "watch",
            "steps_sum": 100,
        }
        phone = {
            "activity_date": date(2026, 1, 10),
            "provider": "apple",
            "source": "Michael's iPhone",
            "device_model": "iPhone18,1",
            "device_type": "phone",
            "steps_sum": 9999,
            "active_energy_sum": 800,
            "hr_avg": 60,
        }
        assert service._filter_by_priority(db, uuid4(), [phone, watch]) == [watch]

    def test_uses_sleep_date_key(self, db: Session, service: SummariesService) -> None:
        entries = [
            {"sleep_date": date(2026, 1, 1), "source": "garmin", "device_model": None},
            {"sleep_date": date(2026, 1, 1), "source": "oura", "device_model": None},
        ]
        result = service._filter_by_priority(db, uuid4(), entries, date_key="sleep_date")
        assert len(result) == 1

    def test_fills_metrics_the_winner_lacks_from_the_next_source(self, db: Session, service: SummariesService) -> None:
        """A phone's steps and a watch's heart rate show on one day; a group the winner has part of stays its own."""
        day = date(2026, 1, 1)
        watch = {"activity_date": day, "provider": "garmin", "device_type": "watch", "steps_sum": 4000}
        watch |= {"active_energy_sum": 300.0, "basal_energy_sum": None, "hr_avg": None}
        band = {"activity_date": day, "provider": "garmin", "device_type": "band", "steps_sum": 9000}
        band |= {"active_energy_sum": 500.0, "basal_energy_sum": 1500.0, "hr_avg": 60, "hr_max": 120, "hr_min": 50}

        [result] = service._filter_by_priority(
            db,
            uuid4(),
            [band, watch],
            fill=(("steps_sum",), ("active_energy_sum", "basal_energy_sum"), ("hr_avg", "hr_max", "hr_min")),
        )

        assert (result["device_type"], result["steps_sum"]) == ("watch", 4000)
        assert (result["active_energy_sum"], result["basal_energy_sum"]) == (300.0, None)
        assert (result["hr_avg"], result["hr_max"], result["hr_min"]) == (60, 120, 50)


# ---------------------------------------------------------------------------
# _get_user_max_hr
# ---------------------------------------------------------------------------


class TestGetUserMaxHr:
    def test_falls_back_to_default_when_no_user(self, db: Session, service: SummariesService) -> None:
        result = service._get_user_max_hr(db, uuid4(), datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert result == 190

    def test_falls_back_to_default_when_no_birth_date(self, db: Session, service: SummariesService) -> None:
        user = UserFactory()
        PersonalRecordFactory(user=user, birth_date=None)
        result = service._get_user_max_hr(db, user.id, datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert result == 190

    def test_calculates_from_birth_date(self, db: Session, service: SummariesService) -> None:
        user = UserFactory()
        PersonalRecordFactory(user=user, birth_date=date(1990, 6, 1))
        ref = datetime(2026, 6, 26, tzinfo=timezone.utc)  # age = 36
        result = service._get_user_max_hr(db, user.id, ref)
        assert result == 220 - 36

    def test_adjusts_when_birthday_not_yet_this_year(self, db: Session, service: SummariesService) -> None:
        user = UserFactory()
        PersonalRecordFactory(user=user, birth_date=date(1990, 12, 31))
        ref = datetime(2026, 6, 26, tzinfo=timezone.utc)  # birthday hasn't happened yet -> age 35
        result = service._get_user_max_hr(db, user.id, ref)
        assert result == 220 - 35


# ---------------------------------------------------------------------------
# get_sleep_summaries
# ---------------------------------------------------------------------------


class TestGetSleepSummaries:
    def _make_sleep_record(self, user: Any, start: str, end: str) -> Any:
        ds = DataSourceFactory(user=user, provider=ProviderName.GARMIN, source="garmin")
        return self._make_session(ds, start, end)

    @staticmethod
    def _make_session(data_source: Any, start: str, end: str) -> Any:
        return EventRecordFactory(
            data_source=data_source,
            category="sleep",
            type="sleep",
            start_datetime=_dt(start),
            end_datetime=_dt(end),
            duration_seconds=int((_dt(end) - _dt(start)).total_seconds()),
            zone_offset="+00:00",
        )

    def test_activity_totals_cut_by_the_bounds_utc_dates_like_the_rest_of_the_summary(
        self, db: Session, service: SummariesService
    ) -> None:
        """end=today at noon excludes today, as the workout and sleep queries of the same summary do."""
        source = DataSourceFactory(provider=ProviderName.GARMIN, source="garmin")
        provider_day(db, source, SeriesType.steps, date(2026, 3, 1), sum=Decimal("1000"))
        provider_day(db, source, SeriesType.steps, date(2026, 3, 2), sum=Decimal("2000"))

        totals = service.get_activity_totals(
            db, source.user_id, _dt("2026-03-01T00:00:00+00:00"), _dt("2026-03-02T12:00:00+00:00")
        )

        assert (totals.days, totals.steps) == (1, 1000)

    def test_a_bad_cursor_is_the_clients_error(self, db: Session, service: SummariesService) -> None:
        with pytest.raises(InvalidCursorError) as raised:
            service.get_sleep_summaries(
                db, uuid4(), _dt("2026-01-01T00:00:00+00:00"), _dt("2026-01-07T00:00:00+00:00"), cursor="?", limit=10
            )
        assert raised.value.status_code == 400

    def test_returns_empty_when_no_data(self, db: Session, service: SummariesService) -> None:
        user = UserFactory()
        result = service.get_sleep_summaries(
            db,
            user.id,
            _dt("2026-01-01T00:00:00+00:00"),
            _dt("2026-01-07T00:00:00+00:00"),
            cursor=None,
            limit=10,
        )
        assert result.data == []
        assert result.pagination.has_more is False

    def test_returns_sleep_record(self, db: Session, service: SummariesService) -> None:
        user = UserFactory()
        record = self._make_sleep_record(user, "2026-01-01T23:00:00+00:00", "2026-01-02T07:00:00+00:00")
        SleepDetailsFactory(event_record=record)

        result = service.get_sleep_summaries(
            db,
            user.id,
            _dt("2026-01-01T00:00:00+00:00"),
            _dt("2026-01-03T00:00:00+00:00"),
            cursor=None,
            limit=10,
        )
        assert len(result.data) == 1
        summary = result.data[0]
        assert summary.duration_minutes == 8 * 60
        assert summary.source.provider == "garmin"

    def test_a_day_value_sent_inside_the_sleep_counts_as_before(self, db: Session, service: SummariesService) -> None:
        """Oura sends the night's SpO2 as a day value at 00:00 UTC, inside the sleep."""
        user = UserFactory()
        record = self._make_sleep_record(user, "2026-01-01T23:00:00+00:00", "2026-01-02T07:00:00+00:00")
        SleepDetailsFactory(event_record=record)
        source = db.get(DataSource, record.data_source_id)
        provider_day(db, source, SeriesType.oxygen_saturation, date(2026, 1, 2), avg=Decimal("96.5"))

        result = service.get_sleep_summaries(
            db, user.id, _dt("2026-01-01T00:00:00+00:00"), _dt("2026-01-03T00:00:00+00:00"), cursor=None, limit=10
        )

        assert [night.avg_spo2_percent for night in result.data] == [96.5]

    def test_does_not_return_other_users_data(self, db: Session, service: SummariesService) -> None:
        user_a = UserFactory()
        user_b = UserFactory()
        self._make_sleep_record(user_b, "2026-01-01T23:00:00+00:00", "2026-01-02T07:00:00+00:00")

        result = service.get_sleep_summaries(
            db,
            user_a.id,
            _dt("2026-01-01T00:00:00+00:00"),
            _dt("2026-01-03T00:00:00+00:00"),
            cursor=None,
            limit=10,
        )
        assert result.data == []

    def test_physio_averages_within_sleep_window(self, db: Session, service: SummariesService) -> None:
        """avg_heart_rate_bpm/avg_hrv_sdnn_ms are computed from series_sample
        samples within [min_start_time, max_end_time), independently per series
        type, and samples outside the window are excluded."""
        user = UserFactory()
        ds = DataSourceFactory(user=user, provider=ProviderName.GARMIN, source="garmin")
        record = EventRecordFactory(
            data_source=ds,
            category="sleep",
            type="sleep",
            start_datetime=_dt("2026-01-01T23:00:00+00:00"),
            end_datetime=_dt("2026-01-02T07:00:00+00:00"),
            duration_seconds=8 * 3600,
            zone_offset="+00:00",
        )
        SleepDetailsFactory(event_record=record)

        hr_type = SeriesTypeDefinitionFactory.get_or_create_heart_rate()
        hrv_type = SeriesTypeDefinitionFactory.get_or_create_heart_rate_variability_sdnn()

        for i, val in enumerate([50, 60, 70]):
            DataPointSeriesFactory(
                data_source=ds,
                series_type=hr_type,
                value=val,
                recorded_at=_dt(f"2026-01-02T0{i}:00:00+00:00"),
            )
        DataPointSeriesFactory(
            data_source=ds,
            series_type=hrv_type,
            value=45,
            recorded_at=_dt("2026-01-02T02:00:00+00:00"),
        )
        # Outside the sleep window - must not affect the average
        DataPointSeriesFactory(
            data_source=ds,
            series_type=hr_type,
            value=200,
            recorded_at=_dt("2026-01-02T12:00:00+00:00"),
        )

        roll_up_everything(db)

        result = service.get_sleep_summaries(
            db,
            user.id,
            _dt("2026-01-01T00:00:00+00:00"),
            _dt("2026-01-03T00:00:00+00:00"),
            cursor=None,
            limit=10,
        )
        assert len(result.data) == 1
        summary = result.data[0]
        assert summary.avg_heart_rate_bpm == 60
        assert summary.avg_hrv_sdnn_ms == 45

    def test_daily_spo2_stamped_at_day_boundary_is_picked_up(self, db: Session, service: SummariesService) -> None:
        """Providers that file one daily SpO2 average stamp it at 00:00 UTC of the
        wake-up day, which falls before the sleep window for anyone west of UTC.
        It is matched by calendar day instead so the night still reports SpO2."""
        user = UserFactory()
        ds = DataSourceFactory(user=user, provider=ProviderName.OURA, source="oura")
        record = EventRecordFactory(
            data_source=ds,
            category="sleep",
            type="sleep",
            # US-eastern night: 23:00 local on Jan 1 -> 07:00 local on Jan 2
            start_datetime=_dt("2026-01-02T04:00:00+00:00"),
            end_datetime=_dt("2026-01-02T12:00:00+00:00"),
            duration_seconds=8 * 3600,
            zone_offset="-05:00",
        )
        SleepDetailsFactory(event_record=record)

        spo2_type = SeriesTypeDefinitionFactory.get_or_create_oxygen_saturation()
        # Oura's daily SpO2 is a day value: the provider's row for Jan 2 in series_aggregate,
        # starting at 00:00 UTC - before this night starts, so only the wake-up-day match finds it.
        period = CalendarPeriod.of_date(date(2026, 1, 2))
        db.add(
            SeriesAggregate(
                id=uuid4(),
                data_source_id=ds.id,
                series_type_definition_id=spo2_type.id,
                period_kind=period.kind,
                period_start=period.start,
                period_end=period.end,
                zone_offset=period.zone_offset,
                computed_by=ComputedBy.PROVIDER,
                avg=Decimal("96"),
                count=1,
            )
        )
        db.flush()

        result = service.get_sleep_summaries(
            db,
            user.id,
            _dt("2026-01-01T00:00:00+00:00"),
            _dt("2026-01-03T00:00:00+00:00"),
            cursor=None,
            limit=10,
        )
        assert len(result.data) == 1
        assert result.data[0].avg_spo2_percent == 96

    def test_in_window_spo2_wins_over_daily_value(self, db: Session, service: SummariesService) -> None:
        """A sample stamped at the day boundary, outside the sleep, is not the night's; a source with
        intra-night samples and no provider day value reports their average."""
        user = UserFactory()
        ds = DataSourceFactory(user=user, provider=ProviderName.GARMIN, source="garmin")
        record = EventRecordFactory(
            data_source=ds,
            category="sleep",
            type="sleep",
            start_datetime=_dt("2026-01-02T04:00:00+00:00"),
            end_datetime=_dt("2026-01-02T12:00:00+00:00"),
            duration_seconds=8 * 3600,
            zone_offset="-05:00",
        )
        SleepDetailsFactory(event_record=record)

        spo2_type = SeriesTypeDefinitionFactory.get_or_create_oxygen_saturation()
        for hour, val in ((5, 94), (6, 96)):
            DataPointSeriesFactory(
                data_source=ds,
                series_type=spo2_type,
                value=val,
                recorded_at=_dt(f"2026-01-02T0{hour}:00:00+00:00"),
            )
        # Day-boundary value that must not drag the average
        DataPointSeriesFactory(
            data_source=ds,
            series_type=spo2_type,
            value=80,
            recorded_at=_dt("2026-01-02T00:00:00+00:00"),
        )

        result = service.get_sleep_summaries(
            db,
            user.id,
            _dt("2026-01-01T00:00:00+00:00"),
            _dt("2026-01-03T00:00:00+00:00"),
            cursor=None,
            limit=10,
        )
        assert len(result.data) == 1
        assert result.data[0].avg_spo2_percent == 95

    def test_a_night_takes_its_own_sleeps_values_the_providers_first(
        self, db: Session, service: SummariesService
    ) -> None:
        """The ring's own night value wins over ours; the watch worn the same night fills nothing (fork: a night
        reports its own data source's vitals only)."""
        user = UserFactory()
        ring = DataSourceFactory(user=user, provider=ProviderName.OURA, source="oura")
        watch = DataSourceFactory(user=user, provider=ProviderName.APPLE, source="apple")
        record = EventRecordFactory(
            data_source=ring,
            category="sleep",
            type="sleep",
            start_datetime=_dt("2026-01-01T23:00:00+00:00"),
            end_datetime=_dt("2026-01-02T07:00:00+00:00"),
            duration_seconds=8 * 3600,
            zone_offset="+00:00",
        )
        SleepDetailsFactory(event_record=record)
        hr_type = SeriesTypeDefinitionFactory.get_or_create_heart_rate()
        hrv_type = SeriesTypeDefinitionFactory.get_or_create_heart_rate_variability_sdnn()
        for hour, value in enumerate([60, 62]):
            DataPointSeriesFactory(
                data_source=ring, series_type=hr_type, value=value, recorded_at=_dt(f"2026-01-02T0{hour}:00:00+00:00")
            )
        DataPointSeriesFactory(
            data_source=watch, series_type=hrv_type, value=45, recorded_at=_dt("2026-01-02T02:00:00+00:00")
        )
        roll_up_everything(db)
        AggregateRepository(EventRecordAggregate).upsert(
            db,
            [
                {
                    "record_id": record.id,
                    "series_type_definition_id": hr_type.id,
                    "computed_by": ComputedBy.PROVIDER,
                    "avg": Decimal("52"),
                    "count": 1,
                }
            ],
        )

        summary = service.get_sleep_summaries(
            db, user.id, _dt("2026-01-01T00:00:00+00:00"), _dt("2026-01-03T00:00:00+00:00"), cursor=None, limit=10
        ).data[0]

        assert (summary.avg_heart_rate_bpm, summary.avg_hrv_sdnn_ms) == (52, None)

    def test_a_night_not_rolled_up_yet_reads_its_own_devices_samples_first(
        self, db: Session, service: SummariesService
    ) -> None:
        """Before the 15-minute rollup has reached the night, the sleep's own watch is read, not the phone."""
        user = UserFactory()
        watch = DataSourceFactory(user=user, provider=ProviderName.APPLE, source="Apple Watch")
        phone = DataSourceFactory(user=user, provider=ProviderName.APPLE, source="iPhone")
        record = EventRecordFactory(
            data_source=watch,
            category="sleep",
            type="sleep",
            start_datetime=_dt("2026-01-01T23:00:00+00:00"),
            end_datetime=_dt("2026-01-02T07:00:00+00:00"),
            duration_seconds=8 * 3600,
            zone_offset="+00:00",
        )
        SleepDetailsFactory(event_record=record)
        hrv_type = SeriesTypeDefinitionFactory.get_or_create_heart_rate_variability_sdnn()
        for source, value in ((watch, 50), (phone, 30)):
            DataPointSeriesFactory(
                data_source=source, series_type=hrv_type, value=value, recorded_at=_dt("2026-01-02T02:00:00+00:00")
            )

        summary = service.get_sleep_summaries(
            db, user.id, _dt("2026-01-01T00:00:00+00:00"), _dt("2026-01-03T00:00:00+00:00"), cursor=None, limit=10
        ).data[0]

        assert summary.avg_hrv_sdnn_ms == 50

    def _pixel_night(self, db: Session) -> tuple[DataSource, Any]:
        """2026-11-05: a Pixel Watch night, 04:30-12:17 UTC (US-eastern), through Google Health."""
        user = UserFactory()
        watch = DataSourceFactory(user=user, provider=ProviderName.GOOGLE_HEALTH, source="google_health_api")
        record = EventRecordFactory(
            data_source=watch,
            category="sleep",
            type="sleep",
            start_datetime=_dt("2026-11-05T04:30:00+00:00"),
            end_datetime=_dt("2026-11-05T12:17:00+00:00"),
            duration_seconds=467 * 60,
            zone_offset="-05:00",
        )
        SleepDetailsFactory(event_record=record)
        return watch, user

    def _night(self, db: Session, service: SummariesService, user: Any) -> Any:
        return service.get_sleep_summaries(
            db, user.id, _dt("2026-11-04T00:00:00+00:00"), _dt("2026-11-06T00:00:00+00:00"), cursor=None, limit=10
        ).data[0]

    def test_the_providers_nightly_hrv_outranks_our_mean_of_part_of_the_night(
        self, db: Session, service: SummariesService
    ) -> None:
        """Google's daily HRV (61.1) is the night's figure; our mean of the only eleven 5-minute windows that
        reached us, the last fifty minutes, is 81.6. The headline is Google's, ours is beside it with its n."""
        watch, user = self._pixel_night(db)
        rmssd = SeriesTypeDefinitionFactory.get_or_create_heart_rate_variability_rmssd()
        tail = [69.9, 98.6, 87.3, 103, 101, 88.8, 88.4, 78.5, 64.5, 47.3, 70.6]
        for i, value in enumerate(tail):
            DataPointSeriesFactory(
                data_source=watch,
                series_type=rmssd,
                value=value,
                recorded_at=_dt("2026-11-05T11:20:00+00:00") + timedelta(minutes=5 * i),
            )
        provider_day(db, watch, SeriesType.heart_rate_variability_rmssd, date(2026, 11, 5), avg=Decimal("61.1"))

        for rolled_up in (False, True):
            if rolled_up:
                roll_up_everything(db)
            night = self._night(db, service, user)

            assert night.avg_hrv_rmssd_ms == pytest.approx(61.1)
            hrv = night.vitals_provenance.avg_hrv_rmssd_ms
            assert hrv.computed_by == ComputedBy.PROVIDER
            assert (hrv.provider.value, hrv.provider.scope) == (pytest.approx(61.1), "day")
            assert (hrv.open_wearables.value, hrv.open_wearables.sample_count) == (pytest.approx(81.6, abs=0.05), 11)

    def test_without_a_provider_figure_the_night_says_we_computed_it(
        self, db: Session, service: SummariesService
    ) -> None:
        watch, user = self._pixel_night(db)
        rmssd = SeriesTypeDefinitionFactory.get_or_create_heart_rate_variability_rmssd()
        for hour, value in ((5, 40), (9, 60)):
            DataPointSeriesFactory(
                data_source=watch, series_type=rmssd, value=value, recorded_at=_dt(f"2026-11-05T0{hour}:00:00+00:00")
            )

        night = self._night(db, service, user)

        hrv = night.vitals_provenance.avg_hrv_rmssd_ms
        assert night.avg_hrv_rmssd_ms == 50
        assert (hrv.computed_by, hrv.provider) == (ComputedBy.OPEN_WEARABLES, None)
        assert (hrv.open_wearables.value, hrv.open_wearables.sample_count) == (50, 2)
        assert night.vitals_provenance.avg_spo2_percent is None

    def test_a_providers_daily_heart_rate_is_a_whole_day_and_does_not_outrank_the_nights_samples(
        self, db: Session, service: SummariesService
    ) -> None:
        """Google's daily heart-rate rollUp is a 24-hour mean: the night's own samples stay first, the day value
        is shown beside them."""
        watch, user = self._pixel_night(db)
        hr = SeriesTypeDefinitionFactory.get_or_create_heart_rate()
        for hour, value in ((5, 50), (9, 54)):
            DataPointSeriesFactory(
                data_source=watch, series_type=hr, value=value, recorded_at=_dt(f"2026-11-05T0{hour}:00:00+00:00")
            )
        provider_day(db, watch, SeriesType.heart_rate, date(2026, 11, 5), avg=Decimal("71"))

        night = self._night(db, service, user)

        heart = night.vitals_provenance.avg_heart_rate_bpm
        assert night.avg_heart_rate_bpm == 52
        assert heart.computed_by == ComputedBy.OPEN_WEARABLES
        assert (heart.provider.value, heart.provider.scope) == (71, "day")

    def test_a_figure_sent_with_the_sleep_is_the_providers_for_that_sleep(
        self, db: Session, service: SummariesService
    ) -> None:
        watch, user = self._pixel_night(db)
        record = db.query(EventRecord).filter(EventRecord.data_source_id == watch.id).one()
        rmssd = SeriesTypeDefinitionFactory.get_or_create_heart_rate_variability_rmssd()
        DataPointSeriesFactory(
            data_source=watch, series_type=rmssd, value=70, recorded_at=_dt("2026-11-05T06:00:00+00:00")
        )
        provider_day(db, watch, SeriesType.heart_rate_variability_rmssd, date(2026, 11, 5), avg=Decimal("61"))
        AggregateRepository(EventRecordAggregate).upsert(
            db,
            [
                {
                    "record_id": record.id,
                    "series_type_definition_id": rmssd.id,
                    "computed_by": ComputedBy.PROVIDER,
                    "avg": Decimal("58"),
                    "count": 1,
                }
            ],
        )

        hrv = self._night(db, service, user).vitals_provenance.avg_hrv_rmssd_ms

        assert (hrv.computed_by, hrv.provider.value, hrv.provider.scope) == (ComputedBy.PROVIDER, 58, "sleep")
        assert (hrv.open_wearables.value, hrv.open_wearables.sample_count) == (70, 1)

    def test_physio_averages_none_without_physio_data(self, db: Session, service: SummariesService) -> None:
        user = UserFactory()
        record = self._make_sleep_record(user, "2026-01-01T23:00:00+00:00", "2026-01-02T07:00:00+00:00")
        SleepDetailsFactory(event_record=record)

        result = service.get_sleep_summaries(
            db,
            user.id,
            _dt("2026-01-01T00:00:00+00:00"),
            _dt("2026-01-03T00:00:00+00:00"),
            cursor=None,
            limit=10,
        )
        assert len(result.data) == 1
        summary = result.data[0]
        assert summary.avg_heart_rate_bpm is None
        assert summary.avg_hrv_sdnn_ms is None
        assert summary.avg_hrv_rmssd_ms is None
        assert summary.avg_respiratory_rate is None
        assert summary.avg_spo2_percent is None

    def test_has_more_flag_and_pagination(self, db: Session, service: SummariesService) -> None:
        user = UserFactory()
        ds = DataSourceFactory(user=user, provider=ProviderName.GARMIN, source="garmin")
        for day in range(1, 6):
            EventRecordFactory(
                data_source=ds,
                category="sleep",
                type="sleep",
                start_datetime=_dt(f"2026-01-{day:02d}T23:00:00+00:00"),
                end_datetime=_dt(f"2026-01-{day + 1:02d}T07:00:00+00:00"),
                duration_seconds=8 * 3600,
                zone_offset="+00:00",
            )

        result = service.get_sleep_summaries(
            db,
            user.id,
            _dt("2026-01-01T00:00:00+00:00"),
            _dt("2026-01-10T00:00:00+00:00"),
            cursor=None,
            limit=3,
        )
        assert len(result.data) == 3
        assert result.pagination.has_more is True
        assert result.pagination.next_cursor is not None

    @staticmethod
    def _timeline(start: str, *blocks: tuple[str, int]) -> list[dict[str, str]]:
        """Back-to-back stage dicts from (stage, minutes) pairs."""
        stages = []
        current = _dt(start)
        for stage, minutes in blocks:
            end = current + timedelta(minutes=minutes)
            stages.append({"stage": stage, "start_time": current.isoformat(), "end_time": end.isoformat()})
            current = end
        return stages

    def _sleep_summary(self, db: Session, service: SummariesService, user: Any) -> Any:
        result = service.get_sleep_summaries(
            db,
            user.id,
            _dt("2026-01-01T00:00:00+00:00"),
            _dt("2026-01-03T00:00:00+00:00"),
            cursor=None,
            limit=10,
        )
        assert len(result.data) == 1
        return result.data[0]

    def test_interruptions_count_from_stage_timeline(self, db: Session, service: SummariesService) -> None:
        user = UserFactory()
        record = self._make_sleep_record(user, "2026-01-01T23:00:00+00:00", "2026-01-02T07:00:00+00:00")
        SleepDetailsFactory(
            event_record=record,
            sleep_stages=self._timeline(
                "2026-01-01T23:00:00+00:00",
                ("awake", 15),
                ("light", 60),
                ("awake", 3),
                ("light", 20),
                ("awake", 5),
                ("deep", 60),
                ("awake", 10),
                ("rem", 45),
                ("awake", 6),
                ("light", 30),
                ("awake", 20),
            ),
        )

        assert self._sleep_summary(db, service, user).interruptions_count == 2

    def test_interruptions_count_is_none_without_main_sleep(self, db: Session, service: SummariesService) -> None:
        user = UserFactory()
        ds = DataSourceFactory(user=user, provider=ProviderName.GARMIN, source="garmin")
        nap = self._make_session(ds, "2026-01-02T14:00:00+00:00", "2026-01-02T15:00:00+00:00")
        SleepDetailsFactory(
            event_record=nap,
            is_nap=True,
            sleep_stages=self._timeline("2026-01-02T14:00:00+00:00", ("light", 20), ("awake", 10), ("light", 30)),
        )

        assert self._sleep_summary(db, service, user).interruptions_count is None

    def test_interruptions_count_is_none_without_stage_timeline(self, db: Session, service: SummariesService) -> None:
        user = UserFactory()
        record = self._make_sleep_record(user, "2026-01-01T23:00:00+00:00", "2026-01-02T07:00:00+00:00")
        SleepDetailsFactory(event_record=record)

        assert self._sleep_summary(db, service, user).interruptions_count is None

    def test_interruptions_count_is_none_without_a_sleep_stage_in_the_timeline(
        self, db: Session, service: SummariesService
    ) -> None:
        user = UserFactory()
        record = self._make_sleep_record(user, "2026-01-01T23:00:00+00:00", "2026-01-02T07:00:00+00:00")
        SleepDetailsFactory(
            event_record=record,
            sleep_stages=self._timeline("2026-01-01T23:00:00+00:00", ("in_bed", 120), ("awake", 60), ("in_bed", 300)),
        )

        assert self._sleep_summary(db, service, user).interruptions_count is None

    def test_interruptions_count_ignores_naps(self, db: Session, service: SummariesService) -> None:
        user = UserFactory()
        ds = DataSourceFactory(user=user, provider=ProviderName.GARMIN, source="garmin")
        night = self._make_session(ds, "2026-01-01T23:00:00+00:00", "2026-01-02T07:00:00+00:00")
        SleepDetailsFactory(
            event_record=night,
            sleep_stages=self._timeline("2026-01-01T23:00:00+00:00", ("light", 60), ("awake", 10), ("deep", 60)),
        )
        nap = self._make_session(ds, "2026-01-02T14:00:00+00:00", "2026-01-02T15:00:00+00:00")
        SleepDetailsFactory(
            event_record=nap,
            is_nap=True,
            sleep_stages=self._timeline("2026-01-02T14:00:00+00:00", ("light", 20), ("awake", 10), ("light", 30)),
        )

        assert self._sleep_summary(db, service, user).interruptions_count == 1

    def test_interruptions_count_sums_main_sessions(self, db: Session, service: SummariesService) -> None:
        user = UserFactory()
        ds = DataSourceFactory(user=user, provider=ProviderName.GARMIN, source="garmin")
        first = self._make_session(ds, "2026-01-01T22:00:00+00:00", "2026-01-02T02:00:00+00:00")
        SleepDetailsFactory(
            event_record=first,
            sleep_stages=self._timeline("2026-01-01T22:00:00+00:00", ("light", 60), ("awake", 10), ("deep", 60)),
        )
        second = self._make_session(ds, "2026-01-02T03:00:00+00:00", "2026-01-02T07:00:00+00:00")
        SleepDetailsFactory(
            event_record=second,
            sleep_stages=self._timeline(
                "2026-01-02T03:00:00+00:00", ("light", 30), ("awake", 8), ("rem", 30), ("awake", 12), ("light", 30)
            ),
        )

        assert self._sleep_summary(db, service, user).interruptions_count == 3

    def test_interruptions_count_is_none_when_a_main_session_has_no_timeline(
        self, db: Session, service: SummariesService
    ) -> None:
        user = UserFactory()
        ds = DataSourceFactory(user=user, provider=ProviderName.GARMIN, source="garmin")
        first = self._make_session(ds, "2026-01-01T22:00:00+00:00", "2026-01-02T02:00:00+00:00")
        SleepDetailsFactory(
            event_record=first,
            sleep_stages=self._timeline("2026-01-01T22:00:00+00:00", ("light", 60), ("awake", 10), ("deep", 60)),
        )
        second = self._make_session(ds, "2026-01-02T03:00:00+00:00", "2026-01-02T07:00:00+00:00")
        SleepDetailsFactory(event_record=second)

        assert self._sleep_summary(db, service, user).interruptions_count is None


class TestSleepPagingPerDate:
    """A page holds `limit` nights, however many sources recorded each one."""

    NIGHTS = [date(2026, 1, day) for day in range(2, 7)]
    START = _dt("2026-01-01T00:00:00+00:00")
    END = _dt("2026-01-31T00:00:00+00:00")

    def _two_sources_per_night(self, db: Session) -> Any:
        """Garmin and Oura both record every night; Garmin wins each one.

        The losing Oura record gets the lower id, so within a night it sorts first:
        a page cut between the two rows would elect Oura.
        """
        priorities = ProviderPriorityRepository(ProviderPriority)
        priorities.upsert(db, ProviderName.GARMIN, 1)
        priorities.upsert(db, ProviderName.OURA, 2)
        user = UserFactory()
        garmin = DataSourceFactory(user=user, provider=ProviderName.GARMIN, source="garmin", device_type="watch")
        oura = DataSourceFactory(user=user, provider=ProviderName.OURA, source="oura", device_type="ring")
        for n, night in enumerate(self.NIGHTS):
            wake = datetime(night.year, night.month, night.day, 7, tzinfo=timezone.utc)
            for ds, id_prefix in ((oura, "00000000"), (garmin, "ffffffff")):
                EventRecordFactory(
                    id=UUID(f"{id_prefix}-0000-0000-0000-{n:012d}"),
                    data_source=ds,
                    category="sleep",
                    type="sleep",
                    start_datetime=wake - timedelta(hours=8),
                    end_datetime=wake,
                    duration_seconds=8 * 3600,
                    zone_offset="+00:00",
                )
        return user

    @pytest.mark.parametrize("limit", [1, 2])
    def test_pages_forward_through_every_night_once(self, db: Session, service: SummariesService, limit: int) -> None:
        user = self._two_sources_per_night(db)

        seen: list[tuple[date, str]] = []
        has_more: list[bool] = []
        cursor = None
        while True:
            page = service.get_sleep_summaries(db, user.id, self.START, self.END, cursor=cursor, limit=limit)
            seen += [(summary.date, summary.source.provider) for summary in page.data]
            has_more.append(page.pagination.has_more)
            if not page.pagination.next_cursor:
                break
            cursor = page.pagination.next_cursor

        assert seen == [(night, "garmin") for night in self.NIGHTS]
        # Only the last page says there is nothing more
        assert has_more == [True] * (math.ceil(len(self.NIGHTS) / limit) - 1) + [False]

    def test_previous_cursor_returns_the_page_before(self, db: Session, service: SummariesService) -> None:
        user = self._two_sources_per_night(db)
        first = service.get_sleep_summaries(db, user.id, self.START, self.END, cursor=None, limit=2)
        second = service.get_sleep_summaries(
            db, user.id, self.START, self.END, cursor=first.pagination.next_cursor, limit=2
        )
        third = service.get_sleep_summaries(
            db, user.id, self.START, self.END, cursor=second.pagination.next_cursor, limit=2
        )

        back = service.get_sleep_summaries(
            db, user.id, self.START, self.END, cursor=third.pagination.previous_cursor, limit=2
        )

        assert sorted((s.date, s.source.provider) for s in back.data) == [
            (night, "garmin") for night in self.NIGHTS[2:4]
        ]
        assert back.pagination.has_more is True

    def test_loads_sessions_for_the_page_nights_only(
        self, db: Session, service: SummariesService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = self._two_sources_per_night(db)
        loaded: list[tuple[date, date]] = []
        load = EventRecordRepository._get_sleep_sessions

        def spy(repo: EventRecordRepository, db_session: Session, user_id: UUID, first: date, last: date) -> Any:
            loaded.append((first, last))
            return load(repo, db_session, user_id, first, last)

        monkeypatch.setattr(EventRecordRepository, "_get_sleep_sessions", spy)
        page = service.get_sleep_summaries(db, user.id, self.START, self.END, cursor=None, limit=2)

        # The page's nights only: not the one read to tell whether there is more, not the whole range
        assert loaded == [(self.NIGHTS[0], self.NIGHTS[1])]
        assert [[s.end_time.date() for s in summary.sessions or []] for summary in page.data] == [
            [night] for night in self.NIGHTS[:2]
        ]

    def test_reads_stage_timelines_for_the_page_main_sleeps_only(
        self, db: Session, service: SummariesService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = self._two_sources_per_night(db)
        garmin = db.query(DataSource).filter_by(user_id=user.id, provider=ProviderName.GARMIN).one()
        nap = EventRecordFactory(
            data_source=garmin,
            category="sleep",
            type="sleep",
            start_datetime=datetime(2026, 1, 2, 14, tzinfo=timezone.utc),
            end_datetime=datetime(2026, 1, 2, 15, tzinfo=timezone.utc),
            duration_seconds=3600,
            zone_offset="+00:00",
        )
        SleepDetailsFactory(event_record=nap, is_nap=True)
        read: list[UUID] = []
        load = EventRecordRepository._sleep_stages

        def spy(db_session: Session, record_ids: list[UUID]) -> Any:
            read.extend(record_ids)
            return load(db_session, record_ids)

        monkeypatch.setattr(EventRecordRepository, "_sleep_stages", staticmethod(spy))
        service.get_sleep_summaries(db, user.id, self.START, self.END, cursor=None, limit=2)

        # Every source's main sleeps on the page's two nights, not the nap. Fork: Oura's too, as the
        # night's source is picked after this read (SummariesService._filter_by_priority, which
        # filter_by_priority=false skips), not in SQL as upstream does.
        assert sorted(read) == sorted(
            UUID(f"{prefix}-0000-0000-0000-{n:012d}") for prefix in ("00000000", "ffffffff") for n in range(2)
        )


# ---------------------------------------------------------------------------
# get_recovery_summaries
# ---------------------------------------------------------------------------


class TestGetRecoverySummaries:
    def test_rmssd_input_is_exposed_as_rmssd_not_sdnn(
        self, db: Session, service: SummariesService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Takes a real session: the service now resolves the relay rule before reading."""
        row = {
            "recovery_date": date(2026, 1, 2),
            "provider": "whoop",
            "source": "whoop",
            "device_model": "WHOOP",
            "device_type": "band",
            "record_id": uuid4(),
            "recorded_at": _dt("2026-01-02T00:00:00+00:00"),
            "recovery_score": 74,
            "resting_heart_rate": 51,
            "hrv_rmssd_milli": 63.2,
            "spo2_percentage": 98.4,
        }
        monkeypatch.setattr(service.health_score_repo, "get_recovery_summaries", lambda *_args, **_kwargs: [row])
        monkeypatch.setattr(service, "_filter_by_priority", lambda *_args, **_kwargs: [row])

        result = service.get_recovery_summaries(
            db_session=db,
            user_id=uuid4(),
            start_date=_dt("2026-01-01T00:00:00+00:00"),
            end_date=_dt("2026-01-03T00:00:00+00:00"),
            cursor=None,
            limit=10,
        )

        summary = result.data[0]
        assert summary.avg_hrv_sdnn_ms is None
        assert summary.avg_hrv_rmssd_ms == 63.2


class TestRecoveryPagingPerDate:
    """A page holds `limit` days, however many providers scored each one."""

    DAYS = [date(2026, 1, day) for day in range(2, 7)]
    START = _dt("2026-01-01T00:00:00+00:00")
    END = _dt("2026-01-31T00:00:00+00:00")

    def _two_providers_per_day(self, db: Session) -> Any:
        """WHOOP and Polar both score every day; WHOOP wins each one.

        The losing Polar score gets the lower id at the same timestamp, so within a day
        it sorts first: a page cut between the two rows would elect Polar.
        """
        priorities = ProviderPriorityRepository(ProviderPriority)
        priorities.upsert(db, ProviderName.WHOOP, 1)
        priorities.upsert(db, ProviderName.POLAR, 2)
        user = UserFactory()
        whoop = DataSourceFactory(user=user, provider=ProviderName.WHOOP, source="whoop", device_type="band")
        polar = DataSourceFactory(user=user, provider=ProviderName.POLAR, source="polar", device_type="watch")
        for n, day in enumerate(self.DAYS):
            scored_at = datetime(day.year, day.month, day.day, 7, tzinfo=timezone.utc)
            for ds, provider, id_prefix in (
                (polar, ProviderName.POLAR, "00000000"),
                (whoop, ProviderName.WHOOP, "ffffffff"),
            ):
                HealthScoreFactory(
                    id=UUID(f"{id_prefix}-0000-0000-0000-{n:012d}"),
                    data_source=ds,
                    provider=provider,
                    category=HealthScoreCategory.RECOVERY,
                    recorded_at=scored_at,
                )
        return user

    @pytest.mark.parametrize("limit", [1, 2])
    def test_pages_forward_through_every_day_once(self, db: Session, service: SummariesService, limit: int) -> None:
        user = self._two_providers_per_day(db)

        seen: list[tuple[date, str]] = []
        has_more: list[bool] = []
        cursor = None
        while True:
            page = service.get_recovery_summaries(db, user.id, self.START, self.END, cursor=cursor, limit=limit)
            seen += [(summary.date, summary.source.provider) for summary in page.data]
            has_more.append(page.pagination.has_more)
            if not page.pagination.next_cursor:
                break
            cursor = page.pagination.next_cursor

        assert seen == [(day, "whoop") for day in self.DAYS]
        # Only the last page says there is nothing more
        assert has_more == [True] * (math.ceil(len(self.DAYS) / limit) - 1) + [False]

    def test_previous_cursor_returns_the_page_before(self, db: Session, service: SummariesService) -> None:
        user = self._two_providers_per_day(db)
        first = service.get_recovery_summaries(db, user.id, self.START, self.END, cursor=None, limit=2)
        second = service.get_recovery_summaries(
            db, user.id, self.START, self.END, cursor=first.pagination.next_cursor, limit=2
        )
        third = service.get_recovery_summaries(
            db, user.id, self.START, self.END, cursor=second.pagination.next_cursor, limit=2
        )

        back = service.get_recovery_summaries(
            db, user.id, self.START, self.END, cursor=third.pagination.previous_cursor, limit=2
        )

        assert sorted((s.date, s.source.provider) for s in back.data) == [
            (self.DAYS[2], "whoop"),
            (self.DAYS[3], "whoop"),
        ]


# ---------------------------------------------------------------------------
# get_activity_summaries
# ---------------------------------------------------------------------------


class TestGetActivitySummaries:
    def test_returns_empty_when_no_data(self, db: Session, service: SummariesService) -> None:
        user = UserFactory()
        result = service.get_activity_summaries(
            db,
            user.id,
            _dt("2026-01-01T00:00:00+00:00"),
            _dt("2026-01-07T00:00:00+00:00"),
            cursor=None,
            limit=10,
        )
        assert result.data == []

    def test_aggregates_steps_for_user(self, db: Session, service: SummariesService) -> None:
        user = UserFactory()
        ds = DataSourceFactory(user=user, provider=ProviderName.APPLE, source="apple_health_sdk")
        steps_type = SeriesTypeDefinitionFactory.get_or_create_steps()

        for i in range(3):
            DataPointSeriesFactory(
                data_source=ds,
                series_type=steps_type,
                value=1000,
                recorded_at=_dt(f"2026-01-01T10:0{i}:00+00:00"),
            )

        roll_up_everything(db)

        result = service.get_activity_summaries(
            db,
            user.id,
            _dt("2026-01-01T00:00:00+00:00"),
            _dt("2026-01-02T00:00:00+00:00"),
            cursor=None,
            limit=10,
        )
        assert len(result.data) == 1
        assert result.data[0].steps == 3000

    def _apple_steps(self, user: Any, values: list[int]) -> Any:
        ds = DataSourceFactory(user=user, provider=ProviderName.APPLE, source="apple_health_sdk")
        steps_type = SeriesTypeDefinitionFactory.get_or_create_steps()
        for i, value in enumerate(values):
            DataPointSeriesFactory(
                data_source=ds, series_type=steps_type, value=value, recorded_at=_dt(f"2026-01-01T10:0{i}:00+00:00")
            )
        return ds

    def _day(self, db: Session, service: SummariesService, user: Any) -> list:
        return service.get_activity_summaries(
            db, user.id, _dt("2026-01-01T00:00:00+00:00"), _dt("2026-01-02T00:00:00+00:00"), cursor=None, limit=10
        ).data

    def test_a_day_without_a_day_row_is_counted_from_the_samples(self, db: Session, service: SummariesService) -> None:
        user = UserFactory()
        self._apple_steps(user, [1000, 250])

        assert [day.steps for day in self._day(db, service, user)] == [1250]

    def test_a_day_row_wins_over_the_samples(self, db: Session, service: SummariesService) -> None:
        user = UserFactory()
        ds = self._apple_steps(user, [1000, 250])
        provider_day(db, ds, SeriesType.steps, date(2026, 1, 1), sum=Decimal("9000"))

        assert [day.steps for day in self._day(db, service, user)] == [9000]

    def test_once_the_rollup_reached_the_history_the_samples_are_not_read(
        self, db: Session, service: SummariesService
    ) -> None:
        user = UserFactory()
        self._apple_steps(user, [1000, 250])
        _rollup_reached_the_history(db)

        assert self._day(db, service, user) == []

    def test_a_queued_day_is_counted_from_its_samples_not_its_old_row(
        self, db: Session, service: SummariesService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Samples newer than the last rollup run: the summary follows them, as the day reads do."""
        user = UserFactory()
        ds = self._apple_steps(user, [1000, 250])
        roll_up_everything(db)
        _rollup_reached_the_history(db)
        DataPointSeriesFactory(
            data_source=ds,
            series_type=SeriesTypeDefinitionFactory.get_or_create_steps(),
            value=500,
            recorded_at=_dt("2026-01-01T12:00:00+00:00"),
        )
        requeue_dirty_days([(ds.id, date(2026, 1, 1))])
        monkeypatch.setattr(rollup_queue, "QUEUED_DAYS_TIMEOUT_SECONDS", 5)

        assert [day.steps for day in self._day(db, service, user)] == [1750]

    def test_includes_days_with_only_a_workout(self, db: Session, service: SummariesService) -> None:
        user = UserFactory()
        ds = DataSourceFactory(user=user, provider=ProviderName.STRAVA, source="strava")
        EventRecordFactory(
            data_source=ds,
            category="workout",
            type="running",
            start_datetime=_dt("2026-01-01T10:00:00+00:00"),
            end_datetime=_dt("2026-01-01T11:00:00+00:00"),
            zone_offset="+00:00",
        )

        result = service.get_activity_summaries(
            db,
            user.id,
            _dt("2026-01-01T00:00:00+00:00"),
            _dt("2026-01-02T00:00:00+00:00"),
            cursor=None,
            limit=10,
        )
        assert [(d.date, d.source.provider, d.steps) for d in result.data] == [(date(2026, 1, 1), "strava", None)]

    def test_takes_elevation_from_the_winning_provider_only(self, db: Session, service: SummariesService) -> None:
        # Without a ranking both tie, and the winner would be whichever row the database returned first.
        priorities = ProviderPriorityRepository(ProviderPriority)
        priorities.upsert(db, ProviderName.GARMIN, 1)
        priorities.upsert(db, ProviderName.STRAVA, 2)
        user = UserFactory()
        steps_type = SeriesTypeDefinitionFactory.get_or_create_steps()
        elevations = {ProviderName.GARMIN: "100", ProviderName.STRAVA: "900"}
        for provider, elevation in elevations.items():
            ds = DataSourceFactory(user=user, provider=provider, source="watch", device_model="Forerunner 965")
            WorkoutDetailsFactory(
                event_record=EventRecordFactory(
                    data_source=ds,
                    category="workout",
                    start_datetime=_dt("2026-01-01T10:00:00+00:00"),
                    end_datetime=_dt("2026-01-01T11:00:00+00:00"),
                    zone_offset="+00:00",
                ),
                total_elevation_gain=Decimal(elevation),
            )
            if provider == ProviderName.GARMIN:
                DataPointSeriesFactory(
                    data_source=ds, series_type=steps_type, value=1000, recorded_at=_dt("2026-01-01T10:00:00+00:00")
                )

        result = service.get_activity_summaries(
            db,
            user.id,
            _dt("2026-01-01T00:00:00+00:00"),
            _dt("2026-01-02T00:00:00+00:00"),
            cursor=None,
            limit=10,
        )
        assert [(d.source.provider, d.elevation_meters) for d in result.data] == [("garmin", 100.0)]

    def test_does_not_return_other_users_data(self, db: Session, service: SummariesService) -> None:
        user_a = UserFactory()
        user_b = UserFactory()
        ds = DataSourceFactory(user=user_b)
        steps_type = SeriesTypeDefinitionFactory.get_or_create_steps()
        DataPointSeriesFactory(
            data_source=ds, series_type=steps_type, value=5000, recorded_at=_dt("2026-01-01T10:00:00+00:00")
        )

        result = service.get_activity_summaries(
            db,
            user_a.id,
            _dt("2026-01-01T00:00:00+00:00"),
            _dt("2026-01-02T00:00:00+00:00"),
            cursor=None,
            limit=10,
        )
        assert result.data == []


class TestActivityPagingWindow:
    """Each page aggregates only a window of days around it, widened as needed."""

    def test_pages_through_sparse_days_without_skipping_or_repeating(
        self, db: Session, service: SummariesService
    ) -> None:
        # A day a month for two years: far sparser than any first window, so every
        # page has to widen it, and the cursor must still hand over cleanly.
        user = UserFactory()
        ds = DataSourceFactory(user=user, provider=ProviderName.APPLE, source="apple_health_sdk")
        steps_type = SeriesTypeDefinitionFactory.get_or_create_steps()
        start = _dt("2024-01-01T00:00:00+00:00")
        days = [start + timedelta(days=30 * i, hours=10) for i in range(25)]
        for at in days:
            DataPointSeriesFactory(data_source=ds, series_type=steps_type, value=1000, recorded_at=at)
        expected = [at.date() for at in days]
        end = _dt("2026-06-01T00:00:00+00:00")

        for order, want in (("desc", list(reversed(expected))), ("asc", expected)):
            seen: list[date] = []
            cursor = None
            pages = []
            while True:
                roll_up_everything(db)
                page = service.get_activity_summaries(db, user.id, start, end, cursor=cursor, limit=3, sort_order=order)
                pages.append(page)
                seen += [summary.date for summary in page.data]
                if not page.pagination.next_cursor:
                    break
                cursor = page.pagination.next_cursor
            assert seen == want

            # Back from the third page lands on the second.
            back = service.get_activity_summaries(
                db, user.id, start, end, cursor=pages[2].pagination.previous_cursor, limit=3, sort_order=order
            )
            assert sorted(summary.date for summary in back.data) == sorted(summary.date for summary in pages[1].data)


class TestActivityTotals:
    def test_adds_up_the_days_the_summaries_return(self, db: Session, service: SummariesService) -> None:
        # The total is defined by the daily summaries, so it is checked against them.
        user = UserFactory()
        ds = DataSourceFactory(user=user, provider=ProviderName.APPLE, source="apple_health_sdk")
        steps_type = SeriesTypeDefinitionFactory.get_or_create_steps()
        start = _dt("2026-01-01T00:00:00+00:00")
        for day, value in ((0, 1000), (1, 3000), (5, 2000)):
            DataPointSeriesFactory(
                data_source=ds, series_type=steps_type, value=value, recorded_at=start + timedelta(days=day, hours=10)
            )
        end = _dt("2026-02-01T00:00:00+00:00")

        roll_up_everything(db)

        totals = service.get_activity_totals(db, user.id, start, end)
        days = service.get_activity_summaries(db, user.id, start, end, cursor=None, limit=100).data

        assert totals.days == len(days) == 3
        assert totals.steps == sum(day.steps or 0 for day in days) == 6000
        assert totals.avg_steps == 2000

    def test_a_day_without_steps_has_none_and_stays_out_of_the_average(
        self, db: Session, service: SummariesService
    ) -> None:
        user = UserFactory()
        ds = DataSourceFactory(user=user, provider=ProviderName.APPLE, source="apple_health_sdk")
        start = _dt("2026-01-01T00:00:00+00:00")
        DataPointSeriesFactory(
            data_source=ds,
            series_type=SeriesTypeDefinitionFactory.get_or_create_steps(),
            value=3000,
            recorded_at=start + timedelta(hours=10),
        )
        DataPointSeriesFactory(
            data_source=ds,
            series_type=SeriesTypeDefinitionFactory.get_or_create_heart_rate(),
            value=60,
            recorded_at=start + timedelta(days=1, hours=10),
        )
        end = _dt("2026-01-03T00:00:00+00:00")

        roll_up_everything(db)

        days = service.get_activity_summaries(db, user.id, start, end, cursor=None, limit=10).data
        totals = service.get_activity_totals(db, user.id, start, end)

        assert [day.steps for day in days] == [3000, None]
        assert (totals.days, totals.avg_steps) == (2, 3000)

    def test_is_empty_without_data(self, db: Session, service: SummariesService) -> None:
        totals = service.get_activity_totals(
            db, UserFactory().id, _dt("2026-01-01T00:00:00+00:00"), _dt("2026-02-01T00:00:00+00:00")
        )
        assert (totals.days, totals.steps, totals.avg_steps) == (0, 0, None)


class TestBodySummaryAverages:
    def test_a_resting_pulse_sent_on_the_sleep_counts_beside_the_day_values(
        self, db: Session, service: SummariesService
    ) -> None:
        """Oura, Whoop, Suunto and Withings send the night's resting pulse on the sleep, not on the day."""
        user = UserFactory()
        watch = DataSourceFactory(user=user, provider=ProviderName.GARMIN, source="garmin")
        ring = DataSourceFactory(user=user, provider=ProviderName.OURA, source="oura")
        yesterday = date.today() - timedelta(days=1)
        provider_day(db, watch, SeriesType.resting_heart_rate, yesterday, avg=Decimal("60"))
        night = EventRecordFactory(
            data_source=ring,
            category="sleep",
            type="sleep",
            start_datetime=datetime.combine(yesterday, datetime.min.time(), timezone.utc) - timedelta(hours=1),
            end_datetime=datetime.combine(yesterday, datetime.min.time(), timezone.utc) + timedelta(hours=7),
            zone_offset="+00:00",
        )
        rhr = SeriesTypeDefinitionFactory.get_or_create_resting_heart_rate()
        AggregateRepository(EventRecordAggregate).upsert(
            db,
            [
                {
                    "record_id": night.id,
                    "series_type_definition_id": rhr.id,
                    "computed_by": ComputedBy.PROVIDER,
                    "avg": Decimal("52"),
                    "count": 1,
                }
            ],
        )

        body = service.get_body_summary(db, user.id, 7, 24)

        assert body is not None
        assert body.averaged.resting_heart_rate_bpm == 56

    def test_a_resting_pulse_without_a_day_row_is_counted_from_the_samples(
        self, db: Session, service: SummariesService
    ) -> None:
        user = UserFactory()
        phone = DataSourceFactory(user=user, provider=ProviderName.APPLE, source="apple_health_sdk")
        rhr = SeriesTypeDefinitionFactory.get_or_create_resting_heart_rate()
        noon = datetime.combine(date.today() - timedelta(days=1), datetime.min.time(), timezone.utc) + timedelta(
            hours=12
        )
        for minutes, value in ((0, 50), (30, 54)):
            DataPointSeriesFactory(
                data_source=phone, series_type=rhr, value=value, recorded_at=noon + timedelta(minutes=minutes)
            )

        body = service.get_body_summary(db, user.id, 7, 24)

        assert body is not None
        assert body.averaged.resting_heart_rate_bpm == 52

    def test_a_day_takes_the_preferred_sources_value_not_the_mean_of_all(
        self, db: Session, service: SummariesService
    ) -> None:
        """Sources measure a vital differently; a day is the preferred one's, never a blend across ranks."""
        user = UserFactory()
        watch = DataSourceFactory(user=user, provider=ProviderName.GARMIN, source="garmin")
        ring = DataSourceFactory(user=user, provider=ProviderName.OURA, source="oura")
        priorities = ProviderPriorityRepository(ProviderPriority)
        priorities.upsert(db, ProviderName.OURA, 1)
        priorities.upsert(db, ProviderName.GARMIN, 2)
        yesterday, before = date.today() - timedelta(days=1), date.today() - timedelta(days=2)
        provider_day(db, watch, SeriesType.resting_heart_rate, yesterday, avg=Decimal("60"))
        provider_day(db, ring, SeriesType.resting_heart_rate, yesterday, avg=Decimal("50"))
        provider_day(db, watch, SeriesType.resting_heart_rate, before, avg=Decimal("62"))

        body = service.get_body_summary(db, user.id, 7, 24)

        assert body is not None
        assert body.averaged.resting_heart_rate_bpm == 56  # the ring's 50, and the watch's 62 on its own day


class TestBodySummarySlowChanging:
    def _reading(self, source: object, series: object, at: datetime, value: str) -> None:
        DataPointSeriesFactory(mapping=source, series_type=series, recorded_at=at, value=Decimal(value))

    def test_the_preferred_source_wins_a_day_both_reported(self, db: Session, service: SummariesService) -> None:
        """Oura's height a second newer than Whoop's must not show under Whoop's name."""
        user = UserFactory()
        whoop = DataSourceFactory(user=user, provider=ProviderName.WHOOP, source="whoop")
        oura = DataSourceFactory(user=user, provider=ProviderName.OURA, source="oura")
        priorities = ProviderPriorityRepository(ProviderPriority)
        priorities.upsert(db, ProviderName.WHOOP, 1)
        priorities.upsert(db, ProviderName.OURA, 2)
        height = SeriesTypeDefinitionFactory.get_or_create_height()
        at = datetime.now(timezone.utc) - timedelta(hours=1)
        self._reading(whoop, height, at, "177.8")
        self._reading(oura, height, at + timedelta(seconds=1), "180")

        body = service.get_body_summary(db, user.id, 7, 24)

        assert body is not None
        assert (body.slow_changing.height_cm, body.source.provider) == (177.8, "whoop")

    def test_a_preferred_sources_old_value_does_not_beat_a_newer_day(
        self, db: Session, service: SummariesService
    ) -> None:
        """Priority decides on the newest day only: a weight typed in a year ago is not today's weight."""
        user = UserFactory()
        apple = DataSourceFactory(user=user, provider=ProviderName.APPLE, source="apple_health_sdk")
        withings = DataSourceFactory(user=user, provider=ProviderName.WITHINGS, source="withings")
        priorities = ProviderPriorityRepository(ProviderPriority)
        priorities.upsert(db, ProviderName.APPLE, 1)
        priorities.upsert(db, ProviderName.WITHINGS, 2)
        weight = SeriesTypeDefinitionFactory.get_or_create_weight()
        now = datetime.now(timezone.utc)
        self._reading(apple, weight, now - timedelta(days=365), "90")
        self._reading(withings, weight, now - timedelta(hours=1), "78.4")

        body = service.get_body_summary(db, user.id, 7, 24)

        assert body is not None
        assert (body.slow_changing.weight_kg, body.source.provider) == (78.4, "withings")
