from jose import jwt

from app.schemas.enums import ProviderName
from app.schemas.model_crud.credentials import (
    OAuthTokenResponse,
    ProviderCredentials,
    ProviderEndpoints,
)
from app.services.provider_credentials import provider_credentials
from app.services.providers.templates.base_oauth import BaseOAuthTemplate


class SuuntoOAuth(BaseOAuthTemplate):
    """Suunto OAuth 2.0 implementation."""

    @property
    def endpoints(self) -> ProviderEndpoints:
        return ProviderEndpoints(
            authorize_url="https://cloudapi-oauth.suunto.com/oauth/authorize",
            token_url="https://cloudapi-oauth.suunto.com/oauth/token",
        )

    @property
    def credentials(self) -> ProviderCredentials:
        return ProviderCredentials(
            client_id=provider_credentials.text("suunto_client_id") or "",
            client_secret=provider_credentials.secret_value("suunto_client_secret"),
            redirect_uri=provider_credentials.redirect_uri(ProviderName.SUUNTO),
            default_scope=provider_credentials.scope("suunto_default_scope"),
            subscription_key=provider_credentials.secret_value("suunto_subscription_key"),
        )

    def _get_provider_user_info(self, token_response: OAuthTokenResponse, user_id: str) -> dict[str, str | None]:
        """Extracts Suunto user info from JWT access token."""
        try:
            # jwt.decode requires a key parameter, but we're not verifying signature
            decoded = jwt.decode(
                token_response.access_token,
                key="",  # Empty key since we're not verifying
                options={"verify_signature": False},
            )
            provider_username = decoded.get("user")
            provider_user_id = decoded.get("sub")
            return {"user_id": provider_user_id, "username": provider_username}
        except Exception:
            return {"user_id": None, "username": None}
