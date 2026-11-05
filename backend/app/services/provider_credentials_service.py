"""Reading and changing provider app settings from the dashboard.

Resolution (which value is in use) lives in ``app/services/provider_credentials.py``;
this module is the admin side: what the dashboard shows, and saving what it sends.
"""

import logging
from urllib.parse import urlparse
from uuid import UUID

from app.config import settings
from app.database import DbSession
from app.models import ProviderCredential
from app.repositories.provider_credential_repository import ProviderCredentialRepository
from app.schemas.model_crud.credentials import (
    CredentialSource,
    ProviderAppFieldRead,
    ProviderAppRead,
    ProviderAppUpdate,
)
from app.services.provider_credentials import (
    CredentialField,
    CredentialKind,
    SavedValues,
    credential_fields,
    encode_value,
    environment_value,
    provider_credentials,
)
from app.services.providers.factory import ProviderFactory
from app.utils.structured_logging import log_structured

logger = logging.getLogger(__name__)

MAX_VALUE_LENGTH = 4096


class ProviderHasNoAppSettingsError(LookupError):
    """The provider is unknown, or connects through a mobile SDK and has no app to register."""


def _fallback(field: CredentialField) -> tuple[CredentialSource, str | None]:
    """Where a field's value comes from when nothing is saved in the dashboard."""
    value = environment_value(field)
    if not value:
        return "unset", None
    if field.kind is CredentialKind.URL:
        legacy_attr = f"{(field.redirect_for or field.provider).value}_redirect_uri"
        return ("environment" if getattr(settings, legacy_attr, None) else "default"), value
    if field.derived_from_secret_key and value == settings.secret_key:
        return "derived", value
    return ("environment" if field.key in settings.model_fields_set else "default"), value


def _hint(value: str) -> str | None:
    return f"…{value[-4:]}" if len(value) >= 12 else None


def _validate(field: CredentialField, value: str) -> None:
    if len(value) > MAX_VALUE_LENGTH:
        raise ValueError(f"{field.label} is longer than {MAX_VALUE_LENGTH} characters")
    if field.kind is CredentialKind.SCOPE:
        if any(c in value for c in "\r\n\t"):
            raise ValueError(f"{field.label} must be on one line")
        return
    if any(c.isspace() for c in value):
        raise ValueError(f"{field.label} must not contain spaces or line breaks")
    if field.kind is CredentialKind.URL:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(f"{field.label} must be a full http(s) URL")


class ProviderCredentialsService:
    def __init__(self) -> None:
        self.repo = ProviderCredentialRepository()
        self.factory = ProviderFactory()

    def _fields(self, provider: str) -> tuple[CredentialField, ...]:
        fields = credential_fields(provider)
        if not fields:
            raise ProviderHasNoAppSettingsError(f"Provider '{provider}' has no app settings to configure")
        return fields

    def read(self, db: DbSession, provider: str) -> ProviderAppRead:
        fields = self._fields(provider)
        rows = self.repo.get_for_provider(db, provider)
        saved = SavedValues.from_rows(list(rows.values()))
        reads = [self._field_read(field, saved, rows.get(field.key)) for field in fields]
        in_use = {read.key: read.is_set for read in reads}
        client_keys = [f.key for f in fields if f.key.endswith(("_client_id", "_client_secret"))]
        return ProviderAppRead(
            provider=provider,
            name=self.factory.get_provider(provider).display_name,
            credentials_configured=all(in_use[key] for key in client_keys),
            fields=reads,
        )

    @staticmethod
    def _field_read(field: CredentialField, saved: SavedValues, row: ProviderCredential | None) -> ProviderAppFieldRead:
        fallback_source, fallback_value = _fallback(field)
        saved_value = saved.values.get(field.key)
        source: CredentialSource = "dashboard" if saved_value else fallback_source
        value = saved_value or fallback_value
        return ProviderAppFieldRead(
            key=field.key,
            label=field.label,
            kind=field.kind.value,
            description=field.description,
            env_var=field.env_var,
            source=source,
            is_set=bool(value),
            value=None if field.is_secret else value,
            hint=_hint(value) if field.is_secret and value and source != "derived" else None,
            fallback_source=fallback_source,
            fallback_value=None if field.is_secret else fallback_value,
            updated_at=row.updated_at if row else None,
            unreadable=field.key in saved.unreadable,
        )

    def update(
        self,
        db: DbSession,
        provider: str,
        payload: ProviderAppUpdate,
        developer_id: UUID | None,
    ) -> ProviderAppRead:
        """Save or clear fields. Raises ValueError on an unknown field or an invalid value."""
        fields = {field.key: field for field in self._fields(provider)}
        unknown = sorted(set(payload.values) - set(fields))
        if unknown:
            raise ValueError(f"Unknown field(s) for {provider}: {', '.join(unknown)}")

        upserts: dict[str, str] = {}
        deletes: set[str] = set()
        for key, raw in payload.values.items():
            field = fields[key]
            value = (raw or "").strip()
            if not value:
                deletes.add(key)
                continue
            _validate(field, value)
            upserts[key] = encode_value(field, value)

        self.repo.apply(db, provider, upserts, deletes, developer_id)
        provider_credentials.invalidate()
        log_structured(
            logger,
            "info",
            "Provider app settings changed in the dashboard",
            action="provider_credentials_update",
            provider=provider,
            developer_id=str(developer_id) if developer_id else None,
            saved=sorted(upserts),
            cleared=sorted(deletes),
        )
        return self.read(db, provider)


provider_credentials_service = ProviderCredentialsService()
