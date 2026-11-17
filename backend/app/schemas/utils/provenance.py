from typing import Any

from pydantic import BaseModel, Field


class SourceProvenance(BaseModel):
    """How one reading reached its provider, as the provider's own record of it says.

    Only Google Health states this today. Its ``dataSource`` names the platform that
    uploaded each point into Google's cloud, so a Health Connect reading read through
    the Google Health API shows ``platform: HEALTH_CONNECT``. That is the cloud copy of
    Health Connect data, as opposed to the ``health_connect`` provider, which holds what
    the mobile SDK read from the phone. Values are Google's, verbatim.
    """

    platform: str | None = Field(
        None,
        description=(
            "The platform that uploaded the reading into the provider's cloud, verbatim. Google Health: "
            "`HEALTH_CONNECT`, `FITBIT`, `HEALTH_KIT`, `FIT`, `FITBIT_WEB_API`, `NEST`, `GOOGLE_WEB_API`, "
            "`GOOGLE_PARTNER_INTEGRATION`."
        ),
        example="HEALTH_CONNECT",
    )
    recording_method: str | None = Field(
        None,
        description=(
            "How the value was produced, verbatim: Google Health's `MANUAL` (typed by a person), "
            "`PASSIVELY_MEASURED`, `ACTIVELY_MEASURED` or `DERIVED`."
        ),
        example="PASSIVELY_MEASURED",
    )
    writing_app: str | None = Field(
        None,
        description="Package name of the app that wrote the reading, where the provider states it.",
        example="com.garmin.android.apps.connectmobile",
    )

    @classmethod
    def from_provider_metadata(cls, provider_metadata: Any) -> "SourceProvenance | None":
        """The provenance a row's ``provider_metadata`` states, or None when it states none."""
        if not isinstance(provider_metadata, dict):
            return None
        values = {
            "platform": provider_metadata.get("platform"),
            "recording_method": provider_metadata.get("recording_method"),
            "writing_app": provider_metadata.get("application"),
        }
        kept = {key: value for key, value in values.items() if isinstance(value, str) and value}
        return cls(**kept) if kept else None
