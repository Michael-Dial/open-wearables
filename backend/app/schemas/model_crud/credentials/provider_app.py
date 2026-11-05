from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

CredentialSource = Literal["dashboard", "environment", "derived", "default", "unset"]


class ProviderAppFieldRead(BaseModel):
    """One setting of a provider's app registration, as the dashboard shows it. Never carries a secret."""

    key: str = Field(..., description="Field key, e.g. 'whoop_client_id'.")
    label: str
    kind: Literal["text", "secret", "url", "scope"]
    description: str | None = None
    env_var: str | None = Field(
        None, description="Environment variable the field falls back to, or null if it has none."
    )
    source: CredentialSource = Field(
        ...,
        description=(
            "Where the value in use comes from: 'dashboard' (saved here), 'environment', 'derived'"
            " (generated from SECRET_KEY), 'default' (built-in or built from API_BASE_URL) or 'unset'."
        ),
    )
    is_set: bool = Field(..., description="Whether the field has a value in use.")
    value: str | None = Field(None, description="Value in use. Always null for secrets.")
    hint: str | None = Field(None, description="Secrets only: the last characters of the value in use.")
    fallback_source: CredentialSource = Field(
        ..., description="Where the value would come from with nothing saved in the dashboard."
    )
    fallback_value: str | None = Field(None, description="That fallback value. Always null for secrets.")
    updated_at: datetime | None = Field(None, description="When the dashboard value was saved.")
    unreadable: bool = Field(
        False,
        description=(
            "A secret is saved in the dashboard but cannot be decrypted (SECRET_KEY changed)."
            " It is ignored until entered again."
        ),
    )


class ProviderAppRead(BaseModel):
    provider: str
    name: str
    credentials_configured: bool = Field(..., description="Whether a client ID and secret are in use.")
    fields: list[ProviderAppFieldRead]


class ProviderAppUpdate(BaseModel):
    """Fields to change. A string saves it; null or an empty string removes the dashboard value
    so the environment applies again. Fields left out are unchanged."""

    values: dict[str, str | None] = Field(
        ...,
        examples=[{"whoop_client_id": "abc123", "whoop_client_secret": "s3cret", "whoop_redirect_uri": None}],
    )
