"""Strategy for the Polar AccessLink v4 connection.

This exists to hold a token, not to sync. The user authorises v4 through the ordinary
OAuth routes — which resolve a strategy by provider name, and which ask for this grant
first whenever someone connects Polar without one — and the *Polar* strategy then
uses that token to read the v4 endpoints, filing everything under the user's existing
Polar source. So this declares no workouts, no 24/7 service and no coverage: a scheduled
sync over a ``polar_v4`` connection has nothing to do, by design.
"""

from app.schemas.enums import ProviderName
from app.services.providers.base_strategy import BaseProviderStrategy, ProviderCapabilities, ProviderCoverage
from app.services.providers.polar.v4_oauth import POLAR_V4_BASE_URL, PolarV4OAuth


class PolarV4Strategy(BaseProviderStrategy):
    """Polar AccessLink v4 — OAuth only; the Polar strategy does the reading."""

    def __init__(self) -> None:
        super().__init__()
        self.oauth = PolarV4OAuth(
            user_repo=self.user_repo,
            connection_repo=self.connection_repo,
            provider_name=self.name,
            api_base_url=self.api_base_url,
        )

    @property
    def name(self) -> str:
        return ProviderName.POLAR_V4.value

    @property
    def display_name(self) -> str:
        # Not "Polar_v4": it is no second Polar account, only the beat-to-beat grant on the
        # one the user already has. Connecting Polar asks for it first (see the oauth routes).
        return "Polar beat-to-beat (v4)"

    @property
    def icon_url(self) -> str:
        return "/static/provider-icons/polar.svg"

    @property
    def api_base_url(self) -> str:
        return POLAR_V4_BASE_URL

    @property
    def capabilities(self) -> ProviderCapabilities:
        # No webhooks: v4 has no push mechanism at all. Live sync for Polar stays on the
        # v3 webhook, which is why this connection advertises nothing.
        return ProviderCapabilities()

    @property
    def coverage(self) -> ProviderCoverage:
        # Everything v4 delivers is declared by the Polar strategy, because that is the
        # provider the data lands under.
        return ProviderCoverage()
