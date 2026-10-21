"""Reference instruments with no provider are written into OW by hand.

A PSG export, a Kubios analysis of a chest strap, an EEG headband's file: the
validation hub scores nights against these, and OW stays the only measurement store
only if they land here, beside the devices they score, under the same attribution.
What these pin down is that attribution (a `manual` connection classified
`reference`, one data source per instrument), that the instrument's stage alphabet
is stored as it was said, and that sending the same night twice stores it once.
"""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import User
from app.schemas.enums import ProviderName
from app.services.provider_settings_service import listed_providers
from app.services.providers.factory import ProviderFactory
from tests.factories import UserConnectionFactory

NIGHT = datetime(2026, 10, 5, 22, 0, tzinfo=timezone.utc)
SLEEP_WINDOW = {"start_date": "2026-10-05T00:00:00Z", "end_date": "2026-10-07T00:00:00Z"}
PSG = "PSG - Compumedics Grael"
KUBIOS = "Polar H10 via Kubios"


def _iso(minutes: int) -> str:
    return (NIGHT + timedelta(minutes=minutes)).isoformat()


def _stage(label: str, start_minute: int, end_minute: int) -> dict:
    return {"stage": label, "start_time": _iso(start_minute), "end_time": _iso(end_minute)}


# Eight hours in OW's own words: 30 awake, 240 light, 120 deep, 90 rem.
OUR_WORDS = [
    _stage("awake", 0, 30),
    _stage("light", 30, 150),
    _stage("deep", 150, 270),
    _stage("rem", 270, 360),
    _stage("light", 360, 480),
]
# The same night as a PSG scorer states it.
PSG_WORDS = [
    _stage("W", 0, 30),
    _stage("N2", 30, 150),
    _stage("N3", 150, 270),
    _stage("R", 270, 360),
    _stage("N2", 360, 480),
]


def _import_sleep(client: TestClient, user: User, headers: dict[str, str], **body: object) -> dict:
    payload = {"device_label": PSG, "stages": OUR_WORDS, **body}
    response = client.post(f"/api/v1/users/{user.id}/import/manual/sleep-stages", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _sleep_sessions(client: TestClient, user: User, headers: dict[str, str]) -> list[dict]:
    response = client.get(
        f"/api/v1/users/{user.id}/events/sleep", headers=headers, params={**SLEEP_WINDOW, "include": "stages"}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _connections(client: TestClient, user: User, headers: dict[str, str]) -> list[dict]:
    response = client.get(f"/api/v1/users/{user.id}/connections", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


class TestSleepStages:
    def test_a_night_lands_on_a_reference_connection_and_reads_back_with_its_stages(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        result = _import_sleep(client, user, api_key_header)

        [connection] = _connections(client, user, api_key_header)
        assert (connection["provider"], connection["account_type"]) == ("manual", "reference")
        assert connection["id"] == result["connection_id"]

        [session] = _sleep_sessions(client, user, api_key_header)
        assert session["id"] == result["record_id"]
        assert session["source"]["provider"] == "manual"
        assert session["source"]["device"] == PSG
        assert session["source"]["data_source_id"] == result["data_source_id"]
        assert session["source"]["user_connection_id"] == result["connection_id"]
        assert [s["stage"] for s in session["sleep_stage_intervals"]] == ["awake", "light", "deep", "rem", "light"]
        assert session["start_time"].startswith("2026-10-05T22:00:00")
        assert session["end_time"].startswith("2026-10-06T06:00:00")
        # In OW's words, so the totals are scored.
        assert result["totals_scored"] is True
        assert session["sleep_duration_seconds"] == 450 * 60
        assert session["time_in_bed_seconds"] == 480 * 60
        assert session["efficiency_percent"] == 93.75

    def test_a_foreign_alphabet_is_kept_verbatim_and_never_folded(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        result = _import_sleep(client, user, api_key_header, stages=PSG_WORDS)

        [session] = _sleep_sessions(client, user, api_key_header)
        intervals = session["sleep_stage_intervals"]
        assert [s["vendor_stage"] for s in intervals] == ["W", "N2", "N3", "R", "N2"]
        # Not light/deep: the fold is the consumer's rule, and the store does not guess.
        assert {s["stage"] for s in intervals} == {"unknown"}
        # Null is not zero: no total is derived from a fold this store did not make.
        assert result["totals_scored"] is False
        assert session["sleep_duration_seconds"] is None
        assert session["efficiency_percent"] is None
        assert session["time_in_bed_seconds"] == 480 * 60

    def test_our_own_words_pass_through_untouched_in_any_case(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        _import_sleep(client, user, api_key_header, stages=[_stage("Deep", 0, 60), _stage("REM", 60, 120)])

        [session] = _sleep_sessions(client, user, api_key_header)
        assert [(s["stage"], s["vendor_stage"]) for s in session["sleep_stage_intervals"]] == [
            ("deep", "Deep"),
            ("rem", "REM"),
        ]

    def test_reimporting_the_same_night_replaces_it_rather_than_storing_it_twice(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        first = _import_sleep(client, user, api_key_header)
        rescored = [*OUR_WORDS[:4], _stage("rem", 360, 480)]
        second = _import_sleep(client, user, api_key_header, stages=rescored)

        assert (first["replaced"], second["replaced"]) == (False, True)
        assert second["record_id"] == first["record_id"]
        assert second["external_id"] == first["external_id"]
        [session] = _sleep_sessions(client, user, api_key_header)
        assert [s["stage"] for s in session["sleep_stage_intervals"]][-1] == "rem"
        assert len(_connections(client, user, api_key_header)) == 1

    def test_an_importer_may_name_its_own_id_for_the_night(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        first = _import_sleep(client, user, api_key_header, external_id="psg-night-17")
        second = _import_sleep(client, user, api_key_header, external_id="psg-night-17", sleep_end=_iso(500))

        assert second["record_id"] == first["record_id"]
        assert second["replaced"] is True
        [session] = _sleep_sessions(client, user, api_key_header)
        assert session["end_time"].startswith("2026-10-06T06:20:00")

    def test_two_instruments_scoring_one_night_are_two_records(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        psg = _import_sleep(client, user, api_key_header, device_label=PSG, stages=PSG_WORDS)
        kubios = _import_sleep(client, user, api_key_header, device_label=KUBIOS)

        assert psg["connection_id"] == kubios["connection_id"]
        assert psg["data_source_id"] != kubios["data_source_id"]
        sessions = _sleep_sessions(client, user, api_key_header)
        assert sorted(s["source"]["device"] for s in sessions) == sorted([PSG, KUBIOS])

    def test_a_record_window_may_be_wider_than_its_stages(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        _import_sleep(client, user, api_key_header, sleep_start=_iso(-15), sleep_end=_iso(495), zone_offset="-04:00")

        [session] = _sleep_sessions(client, user, api_key_header)
        assert session["start_time"].startswith("2026-10-05T21:45:00")
        assert session["time_in_bed_seconds"] == 510 * 60
        assert session["zone_offset"] == "-04:00"

    def test_an_existing_manual_connection_is_reused_and_its_classification_kept(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        existing = UserConnectionFactory(user=user, provider="manual", account_type="validation", access_token=None)
        db.commit()

        result = _import_sleep(client, user, api_key_header)

        assert result["connection_id"] == str(existing.id)
        [connection] = _connections(client, user, api_key_header)
        assert connection["account_type"] == "validation"

    def test_a_named_connection_must_be_this_users_manual_one(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        garmin = UserConnectionFactory(user=user, provider="garmin")
        stranger = UserConnectionFactory(provider="manual", access_token=None)
        db.commit()

        for connection_id in (garmin.id, stranger.id, uuid4()):
            response = client.post(
                f"/api/v1/users/{user.id}/import/manual/sleep-stages",
                headers=api_key_header,
                json={"connection_id": str(connection_id), "device_label": PSG, "stages": OUR_WORDS},
            )
            assert response.status_code == 404, response.text
        assert _sleep_sessions(client, user, api_key_header) == []

    def test_an_inconsistent_body_is_refused(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        bad = [
            {"device_label": PSG, "stages": [_stage("deep", 60, 30)]},
            {"device_label": PSG, "stages": []},
            {"device_label": PSG, "stages": OUR_WORDS, "sleep_start": _iso(100), "sleep_end": _iso(50)},
            {"device_label": "", "stages": OUR_WORDS},
            {
                "device_label": PSG,
                "stages": [{"stage": "deep", "start_time": "2026-10-05T22:00:00", "end_time": _iso(5)}],
            },
        ]
        for body in bad:
            response = client.post(
                f"/api/v1/users/{user.id}/import/manual/sleep-stages", headers=api_key_header, json=body
            )
            assert response.status_code == 400, body
        assert _sleep_sessions(client, user, api_key_header) == []

    def test_an_unknown_user_is_a_404(self, client: TestClient, db: Session, api_key_header: dict[str, str]) -> None:
        response = client.post(
            f"/api/v1/users/{uuid4()}/import/manual/sleep-stages",
            headers=api_key_header,
            json={"device_label": PSG, "stages": OUR_WORDS},
        )
        assert response.status_code == 404

    def test_the_import_needs_a_credential(self, client: TestClient, db: Session, user: User) -> None:
        response = client.post(
            f"/api/v1/users/{user.id}/import/manual/sleep-stages", json={"device_label": PSG, "stages": OUR_WORDS}
        )
        assert response.status_code == 401


def _samples(count: int, step_seconds: int = 1, first: float = 60.0) -> list[dict]:
    return [
        {"recorded_at": (NIGHT + timedelta(seconds=i * step_seconds)).isoformat(), "value": first + i}
        for i in range(count)
    ]


def _import_series(client: TestClient, user: User, headers: dict[str, str], **body: object) -> dict:
    payload = {"device_label": KUBIOS, "series_type": "heart_rate", "samples": _samples(5), **body}
    response = client.post(f"/api/v1/users/{user.id}/import/manual/series", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _timeseries(client: TestClient, user: User, headers: dict[str, str], types: str) -> list[dict]:
    response = client.get(
        f"/api/v1/users/{user.id}/timeseries",
        headers=headers,
        params={
            "types": types,
            "start_time": "2026-10-05T21:00:00Z",
            "end_time": "2026-10-06T07:00:00Z",
            "resolution": "raw",
            "limit": 1000,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


class TestSeries:
    def test_rows_read_back_from_timeseries_under_the_manual_feed(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        result = _import_series(client, user, api_key_header)

        assert (result["received"], result["inserted"], result["updated"]) == (5, 5, 0)
        assert result["route"] == "manual.samples"
        rows = _timeseries(client, user, api_key_header, "heart_rate")
        assert [r["value"] for r in rows] == [60, 61, 62, 63, 64]
        assert {(r["route"], r["route_kind"], r["interval_seconds"]) for r in rows} == {
            ("manual.samples", "intraday", None)
        }
        assert {r["source"]["provider"] for r in rows} == {"manual"}
        assert {r["source"]["device"] for r in rows} == {KUBIOS}
        assert {r["source"]["data_source_id"] for r in rows} == {result["data_source_id"]}
        assert {r["source"]["user_connection_id"] for r in rows} == {result["connection_id"]}
        [connection] = _connections(client, user, api_key_header)
        assert (connection["provider"], connection["account_type"]) == ("manual", "reference")

    def test_a_declared_window_is_stored_on_every_row(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        result = _import_series(
            client,
            user,
            api_key_header,
            series_type="heart_rate_variability_rmssd",
            samples=_samples(3, step_seconds=300, first=48.5),
            interval_seconds=300,
        )

        assert result["route"] == "manual.windows"
        rows = _timeseries(client, user, api_key_header, "heart_rate_variability_rmssd")
        assert [(r["value"], r["interval_seconds"], r["route_kind"]) for r in rows] == [
            (48.5, 300, "window"),
            (49.5, 300, "window"),
            (50.5, 300, "window"),
        ]

    def test_sending_the_same_rows_twice_stores_them_once_and_a_correction_replaces(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        _import_series(client, user, api_key_header)
        again = _import_series(client, user, api_key_header)
        corrected = _samples(5)
        corrected[2]["value"] = 99
        fixed = _import_series(client, user, api_key_header, samples=corrected)

        assert (again["inserted"], again["updated"]) == (0, 5)
        assert (fixed["inserted"], fixed["updated"]) == (0, 5)
        rows = _timeseries(client, user, api_key_header, "heart_rate")
        assert [r["value"] for r in rows] == [60, 61, 99, 63, 64]

    def test_the_rows_are_in_the_bulk_export(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        _import_series(client, user, api_key_header)

        response = client.get(
            f"/api/v1/users/{user.id}/export/series",
            headers=api_key_header,
            params={"start_time": "2026-10-05T21:00:00Z", "end_time": "2026-10-06T07:00:00Z"},
        )
        assert response.status_code == 200, response.text
        exported = [json.loads(line) for line in response.text.splitlines()]
        assert [(r["source"]["provider"], r["route"]) for r in exported] == [("manual", "manual.samples")] * 5

    def test_each_sample_keeps_its_own_offset_unless_one_is_declared(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        local = [{"recorded_at": "2026-10-05T18:00:00-04:00", "value": 70}]
        _import_series(client, user, api_key_header, samples=local)
        _import_series(client, user, api_key_header, device_label="Other", samples=_samples(1), zone_offset="+02:00")

        rows = _timeseries(client, user, api_key_header, "heart_rate")
        assert sorted(r["zone_offset"] for r in rows) == ["+02:00", "-04:00"]

    def test_an_unknown_series_type_is_refused(
        self, client: TestClient, db: Session, user: User, api_key_header: dict[str, str]
    ) -> None:
        response = client.post(
            f"/api/v1/users/{user.id}/import/manual/series",
            headers=api_key_header,
            json={"device_label": KUBIOS, "series_type": "not_a_series", "samples": _samples(1)},
        )
        assert response.status_code == 400
        assert _connections(client, user, api_key_header) == []


class TestManualProviderIsUnlisted:
    """A `manual` connection is an ordinary row to the code that walks every connection, and
    nothing a person connects or an admin configures."""

    def test_the_factory_knows_it_and_it_has_nothing_to_sync(self) -> None:
        strategy = ProviderFactory().get_provider("manual")
        assert strategy.oauth is None
        assert strategy.capabilities.rest_pull is False
        assert strategy.capabilities.client_sdk is False
        assert strategy.default_live_sync_mode is None

    def test_it_is_not_a_provider_an_admin_configures(self) -> None:
        assert ProviderName.MANUAL not in listed_providers()

    def test_it_is_never_inferred_from_a_source_string(self) -> None:
        assert ProviderName.from_source_string("Manual entry") is ProviderName.UNKNOWN

    def test_the_coverage_matrix_leaves_it_out(self, client: TestClient, db: Session) -> None:
        response = client.get("/api/v1/meta/coverage")
        assert response.status_code == 200, response.text
        assert "manual" not in response.json()["providers"]
