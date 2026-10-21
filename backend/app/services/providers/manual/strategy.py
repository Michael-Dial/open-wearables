from app.services.providers.base_strategy import BaseProviderStrategy, ProviderCapabilities


class ManualStrategy(BaseProviderStrategy):
    """The manual reference import: records a person typed or uploaded for an instrument
    that has no API (a PSG export, a Kubios analysis of a chest strap, an EEG headband's
    file). See ``app/services/manual_import_service.py``.

    Metadata only. There is no cloud API, no SDK and nothing to poll: every row arrives
    through `/users/{user_id}/import/manual/*`. The strategy exists so a `manual`
    connection is an ordinary row to the code that walks every connection - the
    periodic sync (which skips it: no ``rest_pull``), the connections listing, the
    priority tables - rather than an unknown provider that raises there.
    """

    @property
    def name(self) -> str:
        return "manual"

    @property
    def display_name(self) -> str:
        return "Manual import"

    @property
    def api_base_url(self) -> str:
        return ""

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(file_import=True)
