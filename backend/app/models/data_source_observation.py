from datetime import datetime
from uuid import UUID

from sqlalchemy import Index
from sqlalchemy.orm import Mapped

from app.database import BaseDbModel
from app.mappings import FKDataSource, FKUser, PrimaryKey, int_zero, str_64, str_255


class DataSourceObservation(BaseDbModel):
    """One distinct description of the hardware and software behind a data source, with when it held.

    A ``data_source`` is keyed by what identifies the stream (writer app, model) and keeps
    a single current value of anything else. Everything a platform says *about* the
    stream beyond that - the accessory's own name, its manufacturer, hardware, firmware
    and software versions, the writing app's version, the OS of the phone that ran it -
    changes over a study without changing which stream it is. A firmware update that
    alters an optical sensor's processing, or an app update that changes how a sleep is
    scored, is invisible once only the latest value is kept, and indistinguishable from
    a change in the person.

    So each distinct combination is a row, dated by the records that carried it
    (``first_recorded_at`` / ``last_recorded_at``, the records' own times, which a late
    history sync fills in correctly) and by when it arrived. Nothing here identifies a
    unit or groups anything: it is provenance, read beside the data, never a key.

    Null is "the platform did not say", never "none": a column is filled only from a
    value the payload carried.
    """

    __tablename__ = "data_source_observation"
    __table_args__ = (
        Index("uq_data_source_observation", "data_source_id", "fingerprint", unique=True),
        Index("ix_data_source_observation_user", "user_id"),
    )

    id: Mapped[PrimaryKey[UUID]]
    user_id: Mapped[FKUser]
    data_source_id: Mapped[FKDataSource]
    # sha256 of the values below, so one combination is one row whatever its length.
    fingerprint: Mapped[str_64]

    # HKSourceRevision / Health Connect DataOrigin: the app that wrote the records.
    source_name: Mapped[str_255 | None]
    source_bundle_id: Mapped[str_255 | None]
    source_version: Mapped[str_255 | None]
    source_product_type: Mapped[str_255 | None]
    source_os_version: Mapped[str_255 | None]

    # HKDevice / Health Connect Device: the hardware the writer attached, if any.
    device_name: Mapped[str_255 | None]
    device_manufacturer: Mapped[str_255 | None]
    device_model: Mapped[str_255 | None]
    device_hardware_version: Mapped[str_255 | None]
    device_firmware_version: Mapped[str_255 | None]
    device_software_version: Mapped[str_255 | None]
    # HKDevice.localIdentifier: scoped to the HealthKit store that wrote it and not
    # stable across a restore or re-pair, so recorded as evidence and never as identity.
    device_local_identifier: Mapped[str_255 | None]
    device_udi: Mapped[str_255 | None]
    device_type_reported: Mapped[str_255 | None]

    recording_method: Mapped[str_255 | None]

    first_recorded_at: Mapped[datetime | None]
    last_recorded_at: Mapped[datetime | None]
    first_received_at: Mapped[datetime]
    last_received_at: Mapped[datetime]
    # Records that carried this combination, summed over syncs. A record pushed twice
    # counts twice, so this says how much evidence there is, not how many samples exist.
    records_reported: Mapped[int_zero]
