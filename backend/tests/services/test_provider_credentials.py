"""Provider app credentials saved in the dashboard, resolved ahead of the environment."""

from contextlib import nullcontext
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ProviderCredential
from app.schemas.enums import ProviderName
from app.schemas.model_crud.credentials import ProviderAppFieldRead, ProviderAppRead, ProviderAppUpdate
from app.services.provider_credentials import (
    FIELDS_BY_KEY,
    ProviderCredentialStore,
    credential_fields,
    provider_credentials,
)
from app.services.provider_credentials_service import ProviderCredentialsService, ProviderHasNoAppSettingsError
from app.services.provider_settings_service import ProviderSettingsService, listed_providers
from app.services.providers.factory import ProviderFactory

service = ProviderCredentialsService()


def save(db: Session, provider: str, **values: str | None) -> None:
    service.update(db, provider, ProviderAppUpdate(values=values), developer_id=None)


class TestRegistry:
    def test_every_cloud_provider_has_client_fields(self) -> None:
        factory = ProviderFactory()
        for provider in listed_providers():
            fields = {f.key for f in credential_fields(provider.value)}
            if factory.get_provider(provider.value).has_cloud_api:
                assert any(k.endswith("_client_id") for k in fields), provider
                assert any(k.endswith("_client_secret") for k in fields), provider
                assert f"{provider.value}_redirect_uri" in fields, provider
            else:
                assert not fields, provider

    def test_env_var_names_match_settings_fields(self) -> None:
        for field in FIELDS_BY_KEY.values():
            if field.env_var:
                assert field.key in type(settings).model_fields


class TestResolution:
    def test_environment_applies_with_nothing_saved(self) -> None:
        with patch.object(settings, "whoop_client_id", "env-id"):
            assert provider_credentials.text("whoop_client_id") == "env-id"

    def test_dashboard_value_wins_and_clearing_restores_environment(self, db: Session) -> None:
        with (
            patch.object(settings, "whoop_client_id", "env-id"),
            patch.object(settings, "whoop_client_secret", SecretStr("env-secret")),
        ):
            save(db, "whoop", whoop_client_id="ui-id", whoop_client_secret="ui-secret")
            assert provider_credentials.text("whoop_client_id") == "ui-id"
            assert provider_credentials.secret_value("whoop_client_secret") == "ui-secret"

            save(db, "whoop", whoop_client_id=None, whoop_client_secret="")
            assert provider_credentials.text("whoop_client_id") == "env-id"
            assert provider_credentials.secret_value("whoop_client_secret") == "env-secret"

    def test_oauth_credentials_and_configured_flag_follow_the_dashboard(self, db: Session) -> None:
        with patch.object(settings, "fitbit_client_id", None), patch.object(settings, "fitbit_client_secret", None):
            strategy = ProviderFactory().get_provider("fitbit")
            assert strategy.credentials_configured is False

            save(db, "fitbit", fitbit_client_id="fb-id", fitbit_client_secret="fb-secret")

            credentials = strategy.oauth.credentials  # ty:ignore[unresolved-attribute]
            assert credentials.client_id == "fb-id"
            assert credentials.client_secret == "fb-secret"
            assert strategy.credentials_configured is True
            listed = {p.provider: p for p in ProviderSettingsService().get_all_providers(db)}
            assert listed["fitbit"].credentials_configured is True

    def test_secret_is_encrypted_at_rest(self, db: Session) -> None:
        save(db, "oura", oura_client_secret="plain-oura-secret")
        row = db.get(ProviderCredential, "oura_client_secret")
        assert row is not None
        assert "plain-oura-secret" not in row.value

    def test_non_secret_is_stored_as_is(self, db: Session) -> None:
        save(db, "oura", oura_client_id="oura-id")
        row = db.get(ProviderCredential, "oura_client_id")
        assert row is not None
        assert row.value == "oura-id"

    def test_secret_from_another_secret_key_is_ignored_and_flagged(self, db: Session) -> None:
        foreign = Fernet(Fernet.generate_key()).encrypt(b"lost").decode()
        db.add(ProviderCredential(key="strava_client_secret", provider="strava", value=foreign, updated_at=_now()))
        db.flush()
        with patch.object(settings, "strava_client_secret", SecretStr("env-secret")):
            assert provider_credentials.secret_value("strava_client_secret") == "env-secret"
            field = _field(service.read(db, "strava"), "strava_client_secret")
        assert field.unreadable is True
        assert field.source == "environment"

    def test_wrong_accessor_or_unknown_key_raises(self) -> None:
        with pytest.raises(ValueError, match="is a secret"):
            provider_credentials.text("whoop_client_secret")
        with pytest.raises(ValueError, match="is not a secret"):
            provider_credentials.secret("whoop_client_id")
        with pytest.raises(KeyError):
            provider_credentials.text("whoop_client_idd")


class TestRedirectUri:
    def test_built_from_api_base_url_by_default(self) -> None:
        with patch.object(settings, "api_base_url", "https://ow.example.org"):
            assert (
                provider_credentials.redirect_uri(ProviderName.WHOOP)
                == "https://ow.example.org/api/v1/oauth/whoop/callback"
            )
            assert (
                provider_credentials.redirect_uri(ProviderName.POLAR_V4)
                == "https://ow.example.org/api/v1/oauth/polar_v4/callback"
            )
            assert (
                provider_credentials.redirect_uri(ProviderName.SENSORBIO)
                == "https://ow.example.org/api/v1/oauth/callback/sensorbio"
            )

    def test_legacy_environment_override_still_applies(self) -> None:
        with patch.object(settings, "garmin_redirect_uri", "https://legacy.example.org/cb"):
            assert provider_credentials.redirect_uri(ProviderName.GARMIN) == "https://legacy.example.org/cb"

    def test_dashboard_override(self, db: Session) -> None:
        save(
            db,
            "polar",
            polar_redirect_uri="https://ui.example.org/polar",
            polar_v4_redirect_uri="https://ui.example.org/polar-v4",
        )
        assert provider_credentials.redirect_uri(ProviderName.POLAR) == "https://ui.example.org/polar"
        assert provider_credentials.redirect_uri(ProviderName.POLAR_V4) == "https://ui.example.org/polar-v4"
        assert ProviderFactory().get_provider("polar").oauth.credentials.redirect_uri == (  # ty:ignore[unresolved-attribute]
            "https://ui.example.org/polar"
        )


class TestStoreCache:
    def test_serves_cached_values_until_invalidated(self, db: Session) -> None:
        store = ProviderCredentialStore(session_factory=lambda: nullcontext(db), ttl_seconds=3600)
        with patch.object(settings, "ultrahuman_client_id", "env-id"):
            assert store.text("ultrahuman_client_id") == "env-id"
            save(db, "ultrahuman", ultrahuman_client_id="ui-id")
            assert store.text("ultrahuman_client_id") == "env-id"
            store.invalidate()
            assert store.text("ultrahuman_client_id") == "ui-id"

    def test_database_failure_falls_back_to_environment(self) -> None:
        def broken() -> nullcontext[Session]:
            raise RuntimeError("db down")

        store = ProviderCredentialStore(session_factory=broken, ttl_seconds=0)
        with patch.object(settings, "whoop_client_id", "env-id"):
            assert store.text("whoop_client_id") == "env-id"


class TestAdminService:
    def test_read_never_carries_a_secret(self, db: Session) -> None:
        save(db, "whoop", whoop_client_secret="super-secret-value-1234")
        read = service.read(db, "whoop")
        secret = _field(read, "whoop_client_secret")
        assert secret.value is None
        assert secret.hint == "…1234"
        assert secret.source == "dashboard"
        assert "super-secret-value" not in read.model_dump_json()

    def test_sources(self, db: Session) -> None:
        with (
            patch.object(settings, "api_base_url", "https://ow.example.org"),
            patch.object(settings, "oura_client_id", None),
        ):
            read = service.read(db, "oura")
        assert _field(read, "oura_client_id").source == "unset"
        assert _field(read, "oura_default_scope").source == "default"
        assert _field(read, "oura_webhook_verification_token").source == "derived"
        assert _field(read, "oura_webhook_verification_token").hint is None
        redirect = _field(read, "oura_redirect_uri")
        assert redirect.source == "default"
        assert redirect.value == "https://ow.example.org/api/v1/oauth/oura/callback"

    def test_rejects_unknown_fields_and_bad_values(self, db: Session) -> None:
        with pytest.raises(ValueError, match="Unknown field"):
            save(db, "whoop", oura_client_id="x")
        with pytest.raises(ValueError, match="full http"):
            save(db, "whoop", whoop_redirect_uri="whoop.example.org/cb")
        with pytest.raises(ValueError, match="spaces"):
            save(db, "whoop", whoop_client_id="abc def")
        with pytest.raises(ValueError, match="one line"):
            save(db, "whoop", whoop_default_scope="read\nwrite")

    def test_sdk_only_provider_has_no_app_settings(self, db: Session) -> None:
        with pytest.raises(ProviderHasNoAppSettingsError):
            service.read(db, "apple")


def _field(read: ProviderAppRead, key: str) -> ProviderAppFieldRead:
    return next(f for f in read.fields if f.key == key)


def _now() -> datetime:
    return datetime.now(timezone.utc)
