"""Beat-to-beat capture: researcher controls and the SDK's view of them."""

from uuid import uuid4

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.models import User
from app.services.sdk_token_service import create_sdk_user_token
from tests.factories import UserFactory


def _sdk_headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_sdk_user_token('app_test', str(user.id))}"}


def _window(start: str = "2026-10-12T02:00:00Z", end: str = "2026-10-12T10:00:00Z", **extra: str) -> dict[str, str]:
    return {"start_at": start, "end_at": end, **extra}


class TestBeatCaptureSetting:
    def test_unconfigured_participant_gets_the_instance_default(
        self, client: TestClient, db: Session, api_v1_prefix: str, api_key_header: dict[str, str]
    ) -> None:
        user = UserFactory()

        response = client.get(f"{api_v1_prefix}/users/{user.id}/beat-capture", headers=api_key_header)

        assert response.status_code == 200
        body = response.json()
        assert body["mode"] == "sleep"
        assert body["lookback_days"] == 7
        assert body["sleep_padding_minutes"] == 30
        assert body["is_default"] is True

    def test_partial_update_keeps_the_other_fields(
        self, client: TestClient, db: Session, api_v1_prefix: str, api_key_header: dict[str, str]
    ) -> None:
        user = UserFactory()
        url = f"{api_v1_prefix}/users/{user.id}/beat-capture"

        first = client.put(url, json={"mode": "all_day"}, headers=api_key_header)
        second = client.put(url, json={"sleep_padding_minutes": 0}, headers=api_key_header)

        assert first.status_code == 200
        assert second.status_code == 200
        body = client.get(url, headers=api_key_header).json()
        assert body["mode"] == "all_day"
        assert body["lookback_days"] == 7
        assert body["sleep_padding_minutes"] == 0
        assert body["is_default"] is False

    def test_lookback_is_bounded(
        self, client: TestClient, db: Session, api_v1_prefix: str, api_key_header: dict[str, str]
    ) -> None:
        user = UserFactory()

        response = client.put(
            f"{api_v1_prefix}/users/{user.id}/beat-capture", json={"lookback_days": 0}, headers=api_key_header
        )

        assert response.status_code == 400

    def test_unknown_user_is_404(
        self, client: TestClient, db: Session, api_v1_prefix: str, api_key_header: dict[str, str]
    ) -> None:
        response = client.get(f"{api_v1_prefix}/users/{uuid4()}/beat-capture", headers=api_key_header)

        assert response.status_code == 404

    def test_requires_auth(self, client: TestClient, db: Session, api_v1_prefix: str) -> None:
        user = UserFactory()

        response = client.get(f"{api_v1_prefix}/users/{user.id}/beat-capture")

        assert response.status_code == 401


class TestBeatCaptureWindows:
    def test_request_and_list(
        self, client: TestClient, db: Session, api_v1_prefix: str, api_key_header: dict[str, str]
    ) -> None:
        user = UserFactory()
        url = f"{api_v1_prefix}/users/{user.id}/beat-capture/windows"

        created = client.post(url, json=_window(note="PSG night 3"), headers=api_key_header)

        assert created.status_code == 201
        body = created.json()
        assert body["status"] == "pending"
        assert body["note"] == "PSG night 3"
        listed = client.get(url, params={"status": "pending"}, headers=api_key_header).json()
        assert [w["id"] for w in listed] == [body["id"]]

    def test_window_must_end_after_it_starts(
        self, client: TestClient, db: Session, api_v1_prefix: str, api_key_header: dict[str, str]
    ) -> None:
        user = UserFactory()

        response = client.post(
            f"{api_v1_prefix}/users/{user.id}/beat-capture/windows",
            json=_window(start="2026-10-12T10:00:00Z", end="2026-10-12T10:00:00Z"),
            headers=api_key_header,
        )

        assert response.status_code == 400

    def test_window_span_is_capped(
        self, client: TestClient, db: Session, api_v1_prefix: str, api_key_header: dict[str, str]
    ) -> None:
        user = UserFactory()

        response = client.post(
            f"{api_v1_prefix}/users/{user.id}/beat-capture/windows",
            json=_window(start="2026-10-01T00:00:00Z", end="2026-10-09T00:00:00Z"),
            headers=api_key_header,
        )

        assert response.status_code == 400

    def test_window_needs_a_timezone(
        self, client: TestClient, db: Session, api_v1_prefix: str, api_key_header: dict[str, str]
    ) -> None:
        user = UserFactory()

        response = client.post(
            f"{api_v1_prefix}/users/{user.id}/beat-capture/windows",
            json=_window(start="2026-10-12T02:00:00", end="2026-10-12T10:00:00"),
            headers=api_key_header,
        )

        assert response.status_code == 400

    def test_cancel_pending_but_not_fulfilled(
        self, client: TestClient, db: Session, api_v1_prefix: str, api_key_header: dict[str, str]
    ) -> None:
        user = UserFactory()
        url = f"{api_v1_prefix}/users/{user.id}/beat-capture/windows"
        pending = client.post(url, json=_window(), headers=api_key_header).json()
        done = client.post(
            url, json=_window(start="2026-10-11T02:00:00Z", end="2026-10-11T10:00:00Z"), headers=api_key_header
        ).json()
        client.post(
            f"{api_v1_prefix}/sdk/users/{user.id}/beat-capture/windows/{done['id']}/fulfilled",
            json={"beats": 10, "series": 1},
            headers=_sdk_headers(user),
        )

        cancelled = client.delete(f"{url}/{pending['id']}", headers=api_key_header)
        conflict = client.delete(f"{url}/{done['id']}", headers=api_key_header)

        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert conflict.status_code == 409

    def test_cancel_another_users_window_is_404(
        self, client: TestClient, db: Session, api_v1_prefix: str, api_key_header: dict[str, str]
    ) -> None:
        owner, other = UserFactory(), UserFactory()
        window = client.post(
            f"{api_v1_prefix}/users/{owner.id}/beat-capture/windows", json=_window(), headers=api_key_header
        ).json()

        response = client.delete(
            f"{api_v1_prefix}/users/{other.id}/beat-capture/windows/{window['id']}", headers=api_key_header
        )

        assert response.status_code == 404


class TestSDKBeatCapture:
    def test_phone_reads_mode_and_only_pending_windows(
        self, client: TestClient, db: Session, api_v1_prefix: str, api_key_header: dict[str, str]
    ) -> None:
        user = UserFactory()
        client.put(f"{api_v1_prefix}/users/{user.id}/beat-capture", json={"mode": "off"}, headers=api_key_header)
        url = f"{api_v1_prefix}/users/{user.id}/beat-capture/windows"
        wanted = client.post(url, json=_window(), headers=api_key_header).json()
        dropped = client.post(
            url, json=_window(start="2026-10-10T02:00:00Z", end="2026-10-10T10:00:00Z"), headers=api_key_header
        ).json()
        client.delete(f"{url}/{dropped['id']}", headers=api_key_header)

        response = client.get(f"{api_v1_prefix}/sdk/users/{user.id}/beat-capture", headers=_sdk_headers(user))

        assert response.status_code == 200
        body = response.json()
        assert body["mode"] == "off"
        assert [w["id"] for w in body["windows"]] == [wanted["id"]]
        assert set(body["windows"][0]) == {"id", "start_at", "end_at"}

    def test_token_for_another_user_is_refused(self, client: TestClient, db: Session, api_v1_prefix: str) -> None:
        user, other = UserFactory(), UserFactory()

        response = client.get(f"{api_v1_prefix}/sdk/users/{user.id}/beat-capture", headers=_sdk_headers(other))

        assert response.status_code == 403

    def test_requires_auth(self, client: TestClient, db: Session, api_v1_prefix: str) -> None:
        user = UserFactory()

        response = client.get(f"{api_v1_prefix}/sdk/users/{user.id}/beat-capture")

        assert response.status_code == 401

    def test_fulfilment_is_recorded_and_idempotent(
        self, client: TestClient, db: Session, api_v1_prefix: str, api_key_header: dict[str, str]
    ) -> None:
        user = UserFactory()
        window = client.post(
            f"{api_v1_prefix}/users/{user.id}/beat-capture/windows", json=_window(), headers=api_key_header
        ).json()
        url = f"{api_v1_prefix}/sdk/users/{user.id}/beat-capture/windows/{window['id']}/fulfilled"

        first = client.post(url, json={"beats": 5400, "series": 90}, headers=_sdk_headers(user))
        again = client.post(url, json={"beats": 5410, "series": 90}, headers=_sdk_headers(user))

        assert first.status_code == 200
        assert first.json()["status"] == "fulfilled"
        assert again.json()["beats_reported"] == 5410
        assert again.json()["fulfilled_at"] == first.json()["fulfilled_at"]
        pending = client.get(f"{api_v1_prefix}/sdk/users/{user.id}/beat-capture", headers=_sdk_headers(user)).json()
        assert pending["windows"] == []

    def test_a_cancelled_window_stays_cancelled(
        self, client: TestClient, db: Session, api_v1_prefix: str, api_key_header: dict[str, str]
    ) -> None:
        user = UserFactory()
        url = f"{api_v1_prefix}/users/{user.id}/beat-capture/windows"
        window = client.post(url, json=_window(), headers=api_key_header).json()
        client.delete(f"{url}/{window['id']}", headers=api_key_header)

        response = client.post(
            f"{api_v1_prefix}/sdk/users/{user.id}/beat-capture/windows/{window['id']}/fulfilled",
            json={"beats": 1, "series": 1},
            headers=_sdk_headers(user),
        )

        assert response.status_code == 200
        assert response.json()["status"] == "cancelled"

    def test_unknown_window_is_404(self, client: TestClient, db: Session, api_v1_prefix: str) -> None:
        user = UserFactory()

        response = client.post(
            f"{api_v1_prefix}/sdk/users/{user.id}/beat-capture/windows/{uuid4()}/fulfilled",
            json={"beats": 1, "series": 1},
            headers=_sdk_headers(user),
        )

        assert response.status_code == 404
