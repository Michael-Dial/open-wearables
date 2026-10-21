"""The bulk export streams every raw sample of a window as `/timeseries` rows.

What these pin down is that the two reads cannot disagree: an NDJSON line is a
`/timeseries` ``data[]`` item, the window bounds are the same, the `types` parsing is
the same, and - unlike the default `/timeseries` read - every source's rows come out,
because a method comparison needs both arms.
"""

import csv
import io
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import DataSource, SeriesTypeDefinition, User
from app.schemas.enums import ProviderName, SeriesType, get_series_type_id
from app.schemas.enums.sample_route import ROUTE_ID_BY_ROUTE, SampleRoute
from app.services.series_export_service import CSV_COLUMNS, SOURCE_COLUMNS
from tests.factories import DataPointSeriesFactory, DataSourceFactory

T0 = datetime(2026, 10, 3, 22, 0, 0, tzinfo=timezone.utc)
WINDOW = {"start_time": "2026-10-03T22:00:00Z", "end_time": "2026-10-03T22:10:00Z"}


def _series(db: Session, series_type: SeriesType) -> SeriesTypeDefinition:
    definition = db.get(SeriesTypeDefinition, get_series_type_id(series_type))
    assert definition is not None
    return definition


def _sample(
    db: Session,
    data_source: DataSource,
    at: datetime,
    value: float,
    series_type: SeriesType = SeriesType.heart_rate,
    route: SampleRoute | None = SampleRoute.GARMIN_ACTIVITY_DETAILS,
    provider_metadata: dict | None = None,
) -> None:
    DataPointSeriesFactory(
        data_source=data_source,
        series_type=_series(db, series_type),
        recorded_at=at,
        value=value,
        route_id=ROUTE_ID_BY_ROUTE[route] if route else None,
        provider_metadata=provider_metadata,
    )


def _two_sources(db: Session, user: User) -> tuple[DataSource, DataSource]:
    """A Garmin and an Apple Watch, both with heart rate over the window, one with steps too."""
    garmin = DataSourceFactory(user=user, provider=ProviderName.GARMIN, source="garmin", device_model="Forerunner 965")
    apple = DataSourceFactory(user=user, provider=ProviderName.APPLE, device_model="Watch7,1")
    for minute in range(3):
        _sample(db, garmin, T0 + timedelta(minutes=minute), 60 + minute)
    _sample(db, garmin, T0 + timedelta(minutes=1), 12, SeriesType.steps, SampleRoute.GARMIN_EPOCHS)
    # The watch samples the same second as the Garmin's first row, and states a window.
    _sample(db, apple, T0, 61, route=SampleRoute.SDK_RECORDS, provider_metadata={"interval_seconds": 60})
    _sample(db, apple, T0 + timedelta(minutes=2, seconds=30), 63, route=SampleRoute.SDK_RECORDS)
    db.commit()
    return garmin, apple


def _export(client: TestClient, user: User, headers: dict[str, str], **params: object) -> list[dict]:
    response = client.get(f"/api/v1/users/{user.id}/export/series", headers=headers, params={**WINDOW, **params})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/x-ndjson"
    lines = response.text.splitlines()
    assert all(line for line in lines), "a blank line is not a row"
    return [json.loads(line) for line in lines]


def _keyed(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (r["timestamp"], r["source"]["data_source_id"], r["type"]))


def _timeseries(client: TestClient, user: User, headers: dict[str, str], **params: object) -> list[dict]:
    response = client.get(
        f"/api/v1/users/{user.id}/timeseries",
        headers=headers,
        params={**WINDOW, "resolution": "raw", "limit": 1000, **params},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


class TestNdjson:
    def test_one_line_per_row_in_the_exact_shape_of_a_timeseries_row(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        _two_sources(db, user)

        exported = _export(client, user, api_key_header)

        # Every source's row, as /timeseries would render it: nothing more, nothing less.
        # (/timeseries breaks a shared second by row id, the export by source, so the
        # comparison is on the set of rows, not their order.)
        assert len(exported) == 6
        assert _keyed(exported) == _keyed(_timeseries(client, user, api_key_header))
        first = exported[0]
        assert {"timestamp", "type", "value", "unit", "source", "route", "route_kind", "interval_seconds"} <= set(first)
        assert {"data_source_id", "provider", "source", "device", "user_connection_id"} <= set(first["source"])

    def test_rows_are_ordered_by_time_then_data_source(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        _two_sources(db, user)

        exported = _export(client, user, api_key_header)

        keys = [(row["timestamp"], row["source"]["data_source_id"]) for row in exported]
        assert keys == sorted(keys)
        # Two sources on one second are both there, not collapsed to the ranked one.
        at_t0 = [row for row in exported if row["timestamp"].startswith("2026-10-03T22:00:00")]
        assert sorted(row["source"]["provider"] for row in at_t0) == ["apple", "garmin"]

    def test_the_feed_and_window_fields_survive(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        _two_sources(db, user)

        exported = _export(client, user, api_key_header)

        apple_first = next(r for r in exported if r["source"]["provider"] == "apple")
        assert (apple_first["route"], apple_first["route_kind"], apple_first["interval_seconds"]) == (
            "sdk.records",
            "intraday",
            60,
        )
        garmin_first = next(r for r in exported if r["source"]["provider"] == "garmin")
        assert (garmin_first["route"], garmin_first["route_kind"]) == ("garmin.activity_details", "workout")

    def test_a_window_with_no_rows_is_an_empty_body(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        assert _export(client, user, api_key_header) == []


class TestTypes:
    def test_repeated_types_select_each_series(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        _two_sources(db, user)

        response = client.get(
            f"/api/v1/users/{user.id}/export/series",
            headers=api_key_header,
            params=[*WINDOW.items(), ("types", "heart_rate"), ("types", "steps")],
        )
        assert response.status_code == 200, response.text
        exported = [json.loads(line) for line in response.text.splitlines()]
        assert sorted(row["type"] for row in exported) == ["heart_rate"] * 5 + ["steps"]

    def test_a_comma_joined_types_list_reads_like_the_repeated_form(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        _two_sources(db, user)

        joined = _export(client, user, api_key_header, types="heart_rate,steps")
        only_steps = _export(client, user, api_key_header, types="steps")

        assert sorted(row["type"] for row in joined) == ["heart_rate"] * 5 + ["steps"]
        assert [row["type"] for row in only_steps] == ["steps"]

    def test_an_unknown_series_name_is_refused(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        response = client.get(
            f"/api/v1/users/{user.id}/export/series",
            headers=api_key_header,
            params={**WINDOW, "types": "heart_rate,not_a_series"},
        )
        # The same refusal /timeseries gives a joined list with an unknown name.
        assert response.status_code == 400


class TestWindowAndFilters:
    def test_bounds_are_start_inclusive_end_exclusive_like_timeseries(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        garmin = DataSourceFactory(user=user, provider=ProviderName.GARMIN, source="garmin")
        end = T0 + timedelta(minutes=10)
        _sample(db, garmin, T0 - timedelta(seconds=1), 1)
        _sample(db, garmin, T0, 2)
        _sample(db, garmin, end - timedelta(seconds=1), 3)
        _sample(db, garmin, end, 4)
        db.commit()

        exported = _export(client, user, api_key_header)

        assert [row["value"] for row in exported] == [2, 3]
        assert _keyed(exported) == _keyed(_timeseries(client, user, api_key_header))

    def test_data_source_id_narrows_to_the_named_sources(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        garmin, apple = _two_sources(db, user)
        other = DataSourceFactory(user=user, provider=ProviderName.OURA, source="oura", device_model="Gen3")
        _sample(db, other, T0 + timedelta(minutes=4), 55, route=SampleRoute.OURA_HEARTRATE)
        db.commit()

        response = client.get(
            f"/api/v1/users/{user.id}/export/series",
            headers=api_key_header,
            params=[*WINDOW.items(), ("data_source_id", str(garmin.id)), ("data_source_id", str(apple.id))],
        )
        assert response.status_code == 200, response.text
        exported = [json.loads(line) for line in response.text.splitlines()]
        assert {row["source"]["data_source_id"] for row in exported} == {str(garmin.id), str(apple.id)}
        assert len(exported) == 6

    def test_another_users_rows_are_not_exported(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        _two_sources(db, user)
        stranger = DataSourceFactory(provider=ProviderName.GARMIN, source="garmin")
        _sample(db, stranger, T0, 99)
        db.commit()

        exported = _export(client, user, api_key_header)

        assert all(row["value"] != 99 for row in exported)


class TestAuth:
    def test_the_export_needs_a_credential(self, client: TestClient, db: Session, user: User) -> None:
        response = client.get(f"/api/v1/users/{user.id}/export/series", params=WINDOW)
        assert response.status_code == 401

    def test_a_developer_token_is_accepted_like_on_timeseries(
        self, client: TestClient, db: Session, user: User, auth_headers: dict[str, str]
    ) -> None:
        _two_sources(db, user)
        assert len(_export(client, user, auth_headers)) == 6


class TestCsv:
    def test_a_header_row_then_one_row_per_sample(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        garmin, _ = _two_sources(db, user)

        response = client.get(
            f"/api/v1/users/{user.id}/export/series",
            headers=api_key_header,
            params={**WINDOW, "format": "csv"},
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/csv")
        rows = list(csv.reader(io.StringIO(response.text)))

        header, body = rows[0], rows[1:]
        assert header == list(CSV_COLUMNS)
        assert header[:4] == ["timestamp", "zone_offset", "type", "value"]
        assert len(body) == 6
        by_name = [dict(zip(header, row, strict=True)) for row in body]
        garmin_rows = [r for r in by_name if r["data_source_id"] == str(garmin.id)]
        assert len(garmin_rows) == 4
        assert garmin_rows[0]["route"] == "garmin.activity_details"
        assert garmin_rows[0]["provider"] == "garmin"
        assert garmin_rows[0]["device"] == "Forerunner 965"
        # Nulls are empty cells, never the word "None".
        assert garmin_rows[0]["interval_seconds"] == ""
        assert "None" not in response.text

    def test_an_empty_window_still_writes_the_header(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        response = client.get(
            f"/api/v1/users/{user.id}/export/series",
            headers=api_key_header,
            params={**WINDOW, "format": "csv"},
        )
        assert response.status_code == 200
        assert response.text.splitlines() == [",".join(CSV_COLUMNS)]

    def test_every_source_column_is_a_field_of_the_attribution_schema(self) -> None:
        """A column named here but missing from SourceMetadata would export blanks silently."""
        from app.schemas.utils import SourceMetadata

        known = set(SourceMetadata.model_fields) | set(SourceMetadata.model_computed_fields)
        assert set(SOURCE_COLUMNS) <= known
