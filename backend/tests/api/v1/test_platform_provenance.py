"""Health Connect read through the Google Health API: each row says its platform, and reads can select it.

Google's ``dataSource.platform`` names the platform that uploaded a point into its cloud, so
Health Connect data reaches Open Wearables through a ``google_health`` connection with no mobile
SDK involved. The platform is kept on each row's ``provider_metadata``; these pin down that it is
returned as ``provenance`` and that ``platform=`` selects those rows, and that reads which cannot
see single rows (period aggregates, priority selections) refuse the filter instead of ignoring it.
"""

import csv
import io
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import DataSource, SeriesTypeDefinition, User
from app.schemas.enums import ProviderName, SeriesType, get_series_type_id
from tests.factories import DataPointSeriesFactory, DataSourceFactory, EventRecordFactory, SleepDetailsFactory

T0 = datetime(2026, 10, 3, 22, 0, 0, tzinfo=timezone.utc)
WINDOW = {"start_time": "2026-10-03T22:00:00Z", "end_time": "2026-10-03T22:10:00Z"}
EVENT_WINDOW = {"start_date": "2026-10-01T00:00:00Z", "end_date": "2026-10-10T00:00:00Z"}

HEALTH_CONNECT = {
    "platform": "HEALTH_CONNECT",
    "recording_method": "PASSIVELY_MEASURED",
    "application": "com.garmin.android.apps.connectmobile",
}
FITBIT = {"platform": "FITBIT", "recording_method": "PASSIVELY_MEASURED"}


def _google(user: User) -> DataSource:
    return DataSourceFactory(
        user=user, provider=ProviderName.GOOGLE_HEALTH, source="google_health_api", device_model="Pixel Watch 3"
    )


def _heart_rate(db: Session, data_source: DataSource, at: datetime, value: float, metadata: dict | None) -> None:
    definition = db.get(SeriesTypeDefinition, get_series_type_id(SeriesType.heart_rate))
    DataPointSeriesFactory(
        data_source=data_source, series_type=definition, recorded_at=at, value=value, provider_metadata=metadata
    )


def _seed_samples(db: Session, user: User) -> None:
    google = _google(user)
    apple = DataSourceFactory(user=user, provider=ProviderName.APPLE, device_model="Watch7,1")
    _heart_rate(db, google, T0, 61, HEALTH_CONNECT)
    _heart_rate(db, google, T0 + timedelta(minutes=1), 62, FITBIT)
    _heart_rate(db, apple, T0 + timedelta(minutes=2), 63, None)
    db.commit()


def _get(client: TestClient, path: str, headers: dict[str, str], params: dict) -> dict:
    response = client.get(path, headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


class TestTimeseries:
    def test_each_row_says_its_platform(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        _seed_samples(db, user)

        rows = _get(client, f"/api/v1/users/{user.id}/timeseries", api_key_header, {**WINDOW, "resolution": "raw"})[
            "data"
        ]

        by_value = {row["value"]: row["provenance"] for row in rows}
        assert by_value == {
            61: {
                "platform": "HEALTH_CONNECT",
                "recording_method": "PASSIVELY_MEASURED",
                "writing_app": "com.garmin.android.apps.connectmobile",
            },
            62: {"platform": "FITBIT", "recording_method": "PASSIVELY_MEASURED", "writing_app": None},
            63: None,
        }

    def test_platform_selects_the_health_connect_rows(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        _seed_samples(db, user)

        rows = _get(
            client,
            f"/api/v1/users/{user.id}/timeseries",
            api_key_header,
            {**WINDOW, "resolution": "raw", "platform": "health_connect"},
        )["data"]

        assert [(row["value"], row["source"]["provider"]) for row in rows] == [(61, "google_health")]

    def test_a_period_resolution_refuses_the_filter(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        response = client.get(
            f"/api/v1/users/{user.id}/timeseries",
            headers=api_key_header,
            params={**WINDOW, "resolution": "1day", "platform": "HEALTH_CONNECT"},
        )

        assert response.status_code == 400
        assert "platform" in response.json()["detail"]

    def test_a_priority_selection_refuses_the_filter(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        response = client.get(
            f"/api/v1/users/{user.id}/timeseries",
            headers=api_key_header,
            params={**WINDOW, "resolution": "raw", "source_selection": "per_range", "platform": "HEALTH_CONNECT"},
        )

        assert response.status_code == 400


class TestExport:
    def test_ndjson_honours_the_filter(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        _seed_samples(db, user)

        response = client.get(
            f"/api/v1/users/{user.id}/export/series",
            headers=api_key_header,
            params={**WINDOW, "platform": "HEALTH_CONNECT"},
        )

        assert response.status_code == 200, response.text
        rows = [json.loads(line) for line in response.text.splitlines()]
        assert [(row["value"], row["provenance"]["platform"]) for row in rows] == [(61, "HEALTH_CONNECT")]

    def test_csv_carries_the_provenance_as_columns(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        _seed_samples(db, user)

        response = client.get(
            f"/api/v1/users/{user.id}/export/series", headers=api_key_header, params={**WINDOW, "format": "csv"}
        )

        assert response.status_code == 200, response.text
        rows = {row["value"]: row for row in csv.DictReader(io.StringIO(response.text))}
        assert "provenance" not in rows["61.0"]
        assert (rows["61.0"]["platform"], rows["61.0"]["writing_app"]) == (
            "HEALTH_CONNECT",
            "com.garmin.android.apps.connectmobile",
        )
        assert rows["62.0"]["platform"] == "FITBIT"
        assert rows["63.0"]["platform"] == ""


class TestEvents:
    def test_workouts_say_and_select_their_platform(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        google = _google(user)
        start = datetime(2026, 10, 4, 7, 0, tzinfo=timezone.utc)
        relayed = EventRecordFactory(
            mapping=google, source_name="HEALTH_CONNECT", start_datetime=start, provider_metadata=HEALTH_CONNECT
        )
        EventRecordFactory(
            mapping=google,
            source_name="FITBIT",
            start_datetime=start + timedelta(days=1),
            provider_metadata=FITBIT,
        )
        db.commit()
        path = f"/api/v1/users/{user.id}/events/workouts"

        every = _get(client, path, api_key_header, EVENT_WINDOW)["data"]
        selected = _get(client, path, api_key_header, {**EVENT_WINDOW, "platform": "HEALTH_CONNECT"})["data"]

        assert {w["provenance"]["platform"] for w in every} == {"HEALTH_CONNECT", "FITBIT"}
        assert [w["id"] for w in selected] == [str(relayed.id)]

    def test_sleep_selects_its_platform_and_per_period_refuses_it(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        google = _google(user)
        nights = []
        for day, metadata in ((4, HEALTH_CONNECT), (5, FITBIT)):
            start = datetime(2026, 10, day, 22, 0, tzinfo=timezone.utc)
            record = EventRecordFactory(
                mapping=google,
                category="sleep",
                type_="sleep_session",
                start_datetime=start,
                end_datetime=start + timedelta(hours=8),
                provider_metadata=metadata,
            )
            SleepDetailsFactory(event_record=record)
            nights.append(record)
        db.commit()
        path = f"/api/v1/users/{user.id}/events/sleep"

        selected = _get(client, path, api_key_header, {**EVENT_WINDOW, "platform": "HEALTH_CONNECT"})["data"]
        refused = client.get(
            path,
            headers=api_key_header,
            params={**EVENT_WINDOW, "platform": "HEALTH_CONNECT", "source_selection": "per_period"},
        )

        assert [(s["id"], s["provenance"]["platform"]) for s in selected] == [(str(nights[0].id), "HEALTH_CONNECT")]
        assert refused.status_code == 400
