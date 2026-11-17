from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.enums import ProviderName


class SourceFilterParams(BaseModel):
    """Filters narrowing a read to one data origin, shared by every query over normalized data."""

    provider: ProviderName | None = Field(None, description="Provider filter")
    source: str | None = Field(None, description="Data source filter")
    device_model: str | None = Field(None, description="Device model filter")
    data_source_id: UUID | None = Field(None, description="Direct data source identifier filter")
    include_redundant_relays: bool = Field(
        False,
        description=(
            "Keep the aggregator's copy of a maker that is also connected directly. By default a "
            "read leaves out relayed data (Garmin or Oura arriving through Apple Health, say) for "
            "the span in which the maker's own API delivered the same series type, so one device "
            "is not counted twice. Nothing is deleted: set this to `true` to read the relayed "
            "copies back - to check the direct route for completeness, or to compare the two paths."
        ),
    )


PLATFORM_FILTER_DESCRIPTION = (
    "Only rows whose provider states they were uploaded through this platform, matched on the row's "
    "`provenance.platform` (case-insensitive). `HEALTH_CONNECT` reads the Health Connect data that "
    "reached Google's cloud and arrived through a `google_health` connection, with no mobile SDK "
    "involved. Only Google Health states a platform today, so every other provider's rows are left out "
    "when this is set."
)


class PlatformFilterParams(BaseModel):
    """A row-level filter on the platform a provider says uploaded each row.

    Kept apart from ``SourceFilterParams`` on purpose: those narrow a read to data sources and
    also apply to queries over ``DataSource`` alone and over per-source aggregates, while the
    platform is recorded on each row (``provider_metadata``). A read that cannot see rows has to
    refuse it rather than ignore it.
    """

    platform: str | None = Field(None, description=PLATFORM_FILTER_DESCRIPTION)

    @field_validator("platform")
    @classmethod
    def _normalize_platform(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().upper()
        return value or None
