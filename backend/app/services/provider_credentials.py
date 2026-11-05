"""Provider app credentials, editable in the dashboard, with the environment as fallback.

Every value an OAuth provider integration is registered with (client ID and secret,
redirect URI, scopes, webhook tokens) is read through ``provider_credentials`` instead
of ``settings`` directly. A value saved in the dashboard (a ``provider_credential`` row)
wins; with no row the environment's value applies, so an instance configured only
through ``.env`` behaves exactly as before.

Each process (API workers, Celery workers, beat) keeps the saved values in memory and
re-reads them at most every ``ttl_seconds``, so a change made in the dashboard reaches
every process within that window. The process that saves a change drops its copy at
once.

Secrets are stored Fernet-encrypted with a key derived from SECRET_KEY. Changing
SECRET_KEY makes them unreadable: they are then ignored (the environment applies again)
and the dashboard flags them for re-entry.
"""

from __future__ import annotations

import base64
import hashlib
import threading
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import ProviderCredential
from app.schemas.enums import ProviderName
from app.utils.structured_logging import get_logger

logger = get_logger(__name__)


class CredentialKind(StrEnum):
    TEXT = "text"
    SECRET = "secret"
    URL = "url"
    SCOPE = "scope"


@dataclass(frozen=True)
class CredentialField:
    key: str
    provider: ProviderName
    label: str
    kind: CredentialKind
    description: str | None = None
    # Set for webhook secrets the backend fills from SECRET_KEY when nothing is configured.
    derived_from_secret_key: bool = False
    # Add-on grant whose redirect URI this is (Polar's AccessLink v4), when not the provider's own.
    redirect_for: ProviderName | None = None

    @property
    def is_secret(self) -> bool:
        return self.kind is CredentialKind.SECRET

    @property
    def env_var(self) -> str | None:
        """The environment variable this field falls back to, or None if it has none."""
        return self.key.upper() if self.key in type(settings).model_fields else None


def _oauth_app_fields(
    provider: ProviderName,
    prefix: str,
    *,
    client_id_label: str = "Client ID",
    client_secret_label: str = "Client secret",
    scope_description: str | None = None,
) -> tuple[CredentialField, ...]:
    return (
        CredentialField(f"{prefix}_client_id", provider, client_id_label, CredentialKind.TEXT),
        CredentialField(f"{prefix}_client_secret", provider, client_secret_label, CredentialKind.SECRET),
        CredentialField(
            f"{provider.value}_redirect_uri",
            provider,
            "Redirect URI",
            CredentialKind.URL,
            "Register exactly this URL as the callback in the provider's developer portal. "
            "Leave it empty to use the one built from API_BASE_URL.",
        ),
        CredentialField(f"{prefix}_default_scope", provider, "Scopes", CredentialKind.SCOPE, scope_description),
    )


def _webhook_secret(provider: ProviderName, key: str, label: str, description: str) -> CredentialField:
    return CredentialField(
        key,
        provider,
        label,
        CredentialKind.SECRET,
        f"{description} Leave it empty to use one derived from SECRET_KEY.",
        derived_from_secret_key=True,
    )


_FIELDS: tuple[CredentialField, ...] = (
    *_oauth_app_fields(
        ProviderName.GARMIN,
        "garmin",
        client_id_label="Consumer key",
        client_secret_label="Consumer secret",
        scope_description="Garmin manages scopes on the app itself; normally left empty.",
    ),
    *_oauth_app_fields(ProviderName.POLAR, "polar"),
    CredentialField(
        "polar_v4_redirect_uri",
        ProviderName.POLAR,
        "AccessLink v4 redirect URI",
        CredentialKind.URL,
        "Second callback Polar's beat-to-beat (v4) grant returns to; register it on the same client. "
        "Leave it empty to use the one built from API_BASE_URL.",
        redirect_for=ProviderName.POLAR_V4,
    ),
    CredentialField("polar_v4_default_scope", ProviderName.POLAR, "AccessLink v4 scopes", CredentialKind.SCOPE),
    *_oauth_app_fields(ProviderName.SUUNTO, "suunto"),
    CredentialField(
        "suunto_subscription_key",
        ProviderName.SUUNTO,
        "Subscription key",
        CredentialKind.SECRET,
        "Ocp-Apim-Subscription-Key from the Suunto API Zone profile.",
    ),
    _webhook_secret(
        ProviderName.SUUNTO,
        "suunto_webhook_secret",
        "Webhook secret",
        "Must match the notification secret set in the Suunto developer portal.",
    ),
    *_oauth_app_fields(ProviderName.WHOOP, "whoop"),
    *_oauth_app_fields(ProviderName.OURA, "oura"),
    _webhook_secret(
        ProviderName.OURA,
        "oura_webhook_verification_token",
        "Webhook verification token",
        "Sent to Oura when webhook subscriptions are created.",
    ),
    *_oauth_app_fields(ProviderName.STRAVA, "strava"),
    _webhook_secret(
        ProviderName.STRAVA,
        "strava_webhook_verify_token",
        "Webhook verify token",
        "Sent to Strava when the push subscription is created.",
    ),
    *_oauth_app_fields(ProviderName.FITBIT, "fitbit"),
    *_oauth_app_fields(ProviderName.GOOGLE_HEALTH, "google"),
    _webhook_secret(
        ProviderName.GOOGLE_HEALTH,
        "google_webhook_secret",
        "Webhook secret",
        "Bearer secret Google echoes on every webhook notification.",
    ),
    CredentialField(
        "google_project_id",
        ProviderName.GOOGLE_HEALTH,
        "GCP project number",
        CredentialKind.TEXT,
        "Project number (not ID), used to register the webhook subscriber.",
    ),
    *_oauth_app_fields(ProviderName.ULTRAHUMAN, "ultrahuman"),
    *_oauth_app_fields(ProviderName.SENSORBIO, "sensorbio"),
    *_oauth_app_fields(ProviderName.WITHINGS, "withings"),
    CredentialField(
        "withings_webhook_token",
        ProviderName.WITHINGS,
        "Webhook token",
        CredentialKind.SECRET,
        "Shared token carried in the notification callback URL. Webhooks stay off while it is empty.",
    ),
)

FIELDS_BY_KEY: dict[str, CredentialField] = {field.key: field for field in _FIELDS}


def credential_fields(provider: str) -> tuple[CredentialField, ...]:
    """The editable fields of a provider, in display order. Empty for SDK-only providers."""
    return tuple(field for field in _FIELDS if field.provider.value == provider)


# --- encryption --------------------------------------------------------------------------


def _fernet() -> Fernet:
    digest = hashlib.sha256(b"open-wearables:provider-credential:" + settings.secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encode_value(field: CredentialField, value: str) -> str:
    """What is stored in the row: ciphertext for a secret, the value itself otherwise."""
    return _fernet().encrypt(value.encode()).decode() if field.is_secret else value


def decode_value(field: CredentialField, stored: str) -> str | None:
    """The plain value of a row, or None when a secret cannot be decrypted with this SECRET_KEY."""
    if not field.is_secret:
        return stored
    try:
        return _fernet().decrypt(stored.encode()).decode()
    except InvalidToken:
        return None


@dataclass(frozen=True)
class SavedValues:
    """Decoded dashboard values, plus the keys whose stored secret could not be decrypted."""

    values: dict[str, str]
    unreadable: frozenset[str]

    @classmethod
    def from_rows(cls, rows: list[ProviderCredential]) -> SavedValues:
        values: dict[str, str] = {}
        unreadable: set[str] = set()
        for row in rows:
            field = FIELDS_BY_KEY.get(row.key)
            if field is None:  # a field this version no longer offers
                continue
            decoded = decode_value(field, row.value)
            if decoded is None:
                unreadable.add(row.key)
            elif decoded:
                values[row.key] = decoded
        return cls(values, frozenset(unreadable))


def load_saved_values(db: Session) -> SavedValues:
    return SavedValues.from_rows(list(db.execute(select(ProviderCredential)).scalars()))


# --- resolution ---------------------------------------------------------------------------


def environment_value(field: CredentialField) -> str | None:
    """The value the environment (or its default) gives a field, ignoring the dashboard."""
    if field.kind is CredentialKind.URL:
        return _environment_redirect_uri(field)
    raw = getattr(settings, field.key, None)
    if isinstance(raw, SecretStr):
        raw = raw.get_secret_value()
    return raw or None


def _environment_redirect_uri(field: CredentialField) -> str:
    provider = field.redirect_for or field.provider
    if provider is ProviderName.SENSORBIO:
        # Sensor Bio has always published this path rather than the generic one; keeping it
        # means an app registered against it keeps working.
        return f"{settings.api_base_url}/api/v1/oauth/callback/sensorbio"
    return settings.oauth_redirect_uri(provider)


class ProviderCredentialStore:
    """Resolves provider credentials: dashboard value first, then the environment."""

    def __init__(
        self,
        session_factory: Callable[[], AbstractContextManager[Session]] = SessionLocal,
        ttl_seconds: float = 15.0,
    ) -> None:
        self.session_factory = session_factory
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._saved = SavedValues({}, frozenset())
        self._loaded_at: float | None = None

    def invalidate(self) -> None:
        """Re-read the saved values on next access."""
        with self._lock:
            self._loaded_at = None

    def _saved_values(self) -> SavedValues:
        if self._fresh():
            return self._saved
        with self._lock:
            if self._fresh():
                return self._saved
            try:
                with self.session_factory() as db:
                    self._saved = load_saved_values(db)
            except Exception as e:
                # Keep serving what was last read (or the environment alone): a database
                # hiccup must not take every provider integration down with it.
                logger.warning(
                    "Could not read provider credentials saved in the dashboard; using the environment",
                    action="provider_credentials_load",
                    error=type(e).__name__,
                )
            self._loaded_at = time.monotonic()
            return self._saved

    def _fresh(self) -> bool:
        return self._loaded_at is not None and time.monotonic() - self._loaded_at < self.ttl_seconds

    def _value(self, field: CredentialField) -> str | None:
        return self._saved_values().values.get(field.key) or environment_value(field)

    def text(self, key: str) -> str | None:
        """A non-secret field (client ID, scope, project number)."""
        field = self._field(key)
        if field.is_secret:
            raise ValueError(f"{key} is a secret; read it with secret()")
        return self._value(field)

    def scope(self, key: str) -> str:
        """A scope field; empty when none is configured."""
        return self.text(key) or ""

    def secret(self, key: str) -> SecretStr | None:
        """A secret field, wrapped so it never lands in a log or repr by accident."""
        field = self._field(key)
        if not field.is_secret:
            raise ValueError(f"{key} is not a secret; read it with text()")
        value = self._value(field)
        return SecretStr(value) if value else None

    def secret_value(self, key: str) -> str:
        """A secret field's plain value, empty when none is configured."""
        secret = self.secret(key)
        return secret.get_secret_value() if secret else ""

    def redirect_uri(self, provider: ProviderName) -> str:
        """The OAuth callback URL a provider (or add-on grant) sends the user back to."""
        for field in _FIELDS:
            if field.kind is CredentialKind.URL and (field.redirect_for or field.provider) is provider:
                return self._value(field) or _environment_redirect_uri(field)
        return settings.oauth_redirect_uri(provider)

    @staticmethod
    def _field(key: str) -> CredentialField:
        try:
            return FIELDS_BY_KEY[key]
        except KeyError:
            raise KeyError(f"{key} is not a provider credential field") from None


provider_credentials = ProviderCredentialStore()
