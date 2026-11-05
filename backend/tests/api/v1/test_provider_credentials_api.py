"""GET/PUT /api/v1/oauth/providers/{provider}/credentials."""

from unittest.mock import patch

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Developer, ProviderCredential
from app.services.provider_credentials import provider_credentials

URL = "/api/v1/oauth/providers/{}/credentials"


def fields_by_key(body: dict) -> dict[str, dict]:
    return {f["key"]: f for f in body["fields"]}


def test_requires_a_developer(client: TestClient) -> None:
    assert client.get(URL.format("whoop")).status_code == 401
    assert client.put(URL.format("whoop"), json={"values": {}}).status_code == 401


def test_read_shows_sources_without_secrets(client: TestClient, auth_headers: dict[str, str]) -> None:
    with (
        patch.object(settings, "whoop_client_id", "env-whoop-id"),
        patch.object(settings, "whoop_client_secret", SecretStr("env-whoop-secret-abcd")),
    ):
        response = client.get(URL.format("whoop"), headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "whoop"
    assert body["credentials_configured"] is True
    fields = fields_by_key(body)
    assert fields["whoop_client_id"]["updated_at"] is None
    assert fields["whoop_client_id"]["value"] == "env-whoop-id"
    assert fields["whoop_client_id"]["source"] == "environment"
    assert fields["whoop_client_id"]["env_var"] == "WHOOP_CLIENT_ID"
    assert fields["whoop_client_secret"]["value"] is None
    assert fields["whoop_client_secret"]["hint"] == "…abcd"
    assert "env-whoop-secret" not in response.text


def test_save_then_clear(client: TestClient, db: Session, developer: Developer, auth_headers: dict[str, str]) -> None:
    with patch.object(settings, "oura_client_id", "env-oura-id"):
        response = client.put(
            URL.format("oura"),
            headers=auth_headers,
            json={
                "values": {
                    "oura_client_id": "  ui-oura-id  ",
                    "oura_client_secret": "ui-oura-secret-9876",
                    "oura_redirect_uri": "https://ow.example.org/api/v1/oauth/oura/callback",
                }
            },
        )
        assert response.status_code == 200, response.text
        fields = fields_by_key(response.json())
        assert fields["oura_client_id"]["value"] == "ui-oura-id"
        assert fields["oura_client_id"]["source"] == "dashboard"
        assert fields["oura_client_id"]["fallback_source"] == "environment"
        assert fields["oura_client_id"]["fallback_value"] == "env-oura-id"
        assert fields["oura_client_secret"]["hint"] == "…9876"
        assert fields["oura_client_secret"]["updated_at"] is not None
        assert "ui-oura-secret" not in response.text

        row = db.get(ProviderCredential, "oura_client_id")
        assert row is not None
        assert row.updated_by_id == developer.id
        assert provider_credentials.text("oura_client_id") == "ui-oura-id"

        response = client.put(URL.format("oura"), headers=auth_headers, json={"values": {"oura_client_id": None}})
        assert response.status_code == 200
        fields = fields_by_key(response.json())
        assert fields["oura_client_id"]["source"] == "environment"
        assert fields["oura_client_secret"]["source"] == "dashboard"  # untouched
        assert provider_credentials.text("oura_client_id") == "env-oura-id"


def test_validation_errors_are_400(client: TestClient, auth_headers: dict[str, str]) -> None:
    bad = client.put(URL.format("strava"), headers=auth_headers, json={"values": {"strava_redirect_uri": "nope"}})
    assert bad.status_code == 400
    assert "Redirect URI" in bad.json()["detail"]

    unknown = client.put(URL.format("strava"), headers=auth_headers, json={"values": {"whoop_client_id": "x"}})
    assert unknown.status_code == 400


def test_sdk_only_or_unknown_provider_is_404(client: TestClient, auth_headers: dict[str, str]) -> None:
    assert client.get(URL.format("apple"), headers=auth_headers).status_code == 404
    assert client.get(URL.format("nope"), headers=auth_headers).status_code == 404
    assert client.put(URL.format("apple"), headers=auth_headers, json={"values": {}}).status_code == 404


def test_google_health_url_slug(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = client.get(URL.format("google_health"), headers=auth_headers)
    assert response.status_code == 200
    assert "google_client_id" in fields_by_key(response.json())
