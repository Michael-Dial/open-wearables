"""Responses for what platforms reported about the hardware and software behind a data source."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SourceObservationResponse(BaseModel):
    """One distinct description of a data source's hardware and software, and when it held.

    Every field is what the platform sent, verbatim; null means it sent nothing for that
    field, never that the value was empty. A firmware or app update shows as a new row
    whose recorded span starts where the previous one's ends.
    """

    id: UUID
    data_source_id: UUID
    source_name: str | None = Field(None, description="HKSource.name: the writing app's or device's display name.")
    source_bundle_id: str | None = Field(
        None,
        description="Writer's bundle or package id.",
        examples=["com.apple.heartratecoordinatord"],
    )
    source_version: str | None = Field(None, description="Version of the writing app (HKSourceRevision.version).")
    source_product_type: str | None = Field(
        None, description="Hardware the writing app ran on (HKSourceRevision.productType).", examples=["iPhone18,1"]
    )
    source_os_version: str | None = Field(None, description="OS version the writing app ran on.", examples=["26.1.0"])
    device_name: str | None = Field(None, description="HKDevice.name.", examples=["Michael's AirPods Pro"])
    device_manufacturer: str | None = Field(None, examples=["Apple Inc."])
    device_model: str | None = Field(None, description="HKDevice.model, verbatim.", examples=["AirPods Pro"])
    device_hardware_version: str | None = None
    device_firmware_version: str | None = None
    device_software_version: str | None = None
    device_local_identifier: str | None = Field(
        None,
        description=(
            "HKDevice.localIdentifier. Scoped to one HealthKit store and not stable across a restore "
            "or re-pair: evidence, never an identity."
        ),
    )
    device_udi: str | None = Field(None, description="HKDevice.udiDeviceIdentifier, for regulated devices.")
    device_type_reported: str | None = Field(None, description="Device type the platform or SDK declared.")
    recording_method: str | None = None
    first_recorded_at: datetime | None = Field(None, description="Earliest record time that carried this description.")
    last_recorded_at: datetime | None = Field(None, description="Latest record time that carried this description.")
    first_received_at: datetime
    last_received_at: datetime
    records_reported: int = Field(
        ...,
        description="Records that carried it, summed over syncs. A record pushed twice counts twice.",
    )

    model_config = {"from_attributes": True}


class SourceObservationListResponse(BaseModel):
    items: list[SourceObservationResponse]
    total: int
