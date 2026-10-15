"""Archival leaves beat-to-beat series live.

Archival averages a day of a series into one archive row and deletes the live rows. For
RR and pulse-to-pulse intervals that average keeps nothing a validation study can use,
so those series are skipped and only retention removes them.
"""

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import DataPointSeries, DataPointSeriesArchive, SeriesTypeDefinition
from app.repositories.archival_repository import DataPointSeriesArchiveRepository
from app.schemas.enums import SeriesType, get_series_type_id
from tests.factories import DataPointSeriesFactory, DataSourceFactory, SeriesTypeDefinitionFactory


def _series(db: Session, series_type: SeriesType) -> SeriesTypeDefinition:
    return db.query(SeriesTypeDefinition).filter(SeriesTypeDefinition.id == get_series_type_id(series_type)).one()


def test_beat_intervals_stay_live_while_other_series_archive(db: Session) -> None:
    source = DataSourceFactory()
    old_day = datetime(2026, 1, 5, 3, 0, tzinfo=timezone.utc)
    heart_rate = SeriesTypeDefinitionFactory.get_or_create_heart_rate()
    for minute in range(3):
        DataPointSeriesFactory(data_source=source, series_type=heart_rate, recorded_at=old_day.replace(minute=minute))
    for series_type in (SeriesType.pulse_to_pulse_interval, SeriesType.rr_interval):
        for millis in (0, 850, 1700):
            DataPointSeriesFactory(
                data_source=source,
                series_type=_series(db, series_type),
                recorded_at=old_day.replace(second=millis // 1000, microsecond=(millis % 1000) * 1000),
                value=Decimal("850"),
            )

    deleted = DataPointSeriesArchiveRepository().archive_data_before(db, date(2026, 2, 1))

    assert deleted.rows == 3
    live_codes = {
        code
        for (code,) in db.query(SeriesTypeDefinition.code)
        .join(DataPointSeries, DataPointSeries.series_type_definition_id == SeriesTypeDefinition.id)
        .filter(DataPointSeries.data_source_id == source.id)
        .all()
    }
    assert live_codes == {"pulse_to_pulse_interval", "rr_interval"}
    archived = db.query(DataPointSeriesArchive).filter(DataPointSeriesArchive.data_source_id == source.id).all()
    assert [a.series_type_definition_id for a in archived] == [heart_rate.id]


def test_a_source_holding_only_beats_does_not_stall_archival(db: Session) -> None:
    """Each pass takes ten sources at a time; one with nothing archivable must not end the pass early."""
    old_day = datetime(2026, 1, 5, 3, 0, tzinfo=timezone.utc)
    ppi = _series(db, SeriesType.pulse_to_pulse_interval)
    for _ in range(12):
        DataPointSeriesFactory(data_source=DataSourceFactory(), series_type=ppi, recorded_at=old_day)
    tail_source = DataSourceFactory()
    DataPointSeriesFactory(data_source=tail_source, recorded_at=old_day)

    deleted = DataPointSeriesArchiveRepository().archive_data_before(db, date(2026, 2, 1))

    assert deleted.rows == 1
    assert db.query(DataPointSeries).filter(DataPointSeries.data_source_id == tail_source.id).count() == 0
