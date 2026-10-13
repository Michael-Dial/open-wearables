from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import BaseDbModel
from app.mappings import FKUser, Indexed, PrimaryKey, str_255


class BeatCaptureSetting(BaseDbModel):
    """Whether a participant's phone uploads Apple Watch beat-to-beat intervals, and when.

    The beats live in HealthKit on the phone, so the decision is the phone's to carry
    out; this row is what it reads before each sync (GET /sdk/users/{id}/beat-capture).
    A researcher sets it per participant, which keeps athletes out of the loop.

    ``mode``:
    - ``off``: nothing automatic. Requested windows are still fulfilled.
    - ``sleep``: every heartbeat series that overlaps a HealthKit sleep session, padded
      by ``sleep_padding_minutes`` either side.
    - ``all_day``: every heartbeat series.

    ``lookback_days`` bounds how far back the automatic modes reach on the phone. A
    window request is not bound by it.

    No row means the instance default (``BEAT_CAPTURE_DEFAULT_MODE``), so a participant
    nobody configured behaves the same as one set to that mode.
    """

    __tablename__ = "beat_capture_setting"
    __table_args__ = (
        CheckConstraint("mode IN ('off', 'sleep', 'all_day')", name="ck_beat_capture_setting_mode"),
        CheckConstraint("lookback_days BETWEEN 1 AND 30", name="ck_beat_capture_setting_lookback"),
        CheckConstraint("sleep_padding_minutes BETWEEN 0 AND 240", name="ck_beat_capture_setting_padding"),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16))
    lookback_days: Mapped[int] = mapped_column(Integer)
    sleep_padding_minutes: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime]


class BeatCaptureWindow(BaseDbModel):
    """A span of time whose beat-to-beat intervals a researcher wants from one participant.

    The phone fulfils it on its next sync whatever the participant's mode, uploads every
    heartbeat series overlapping ``[start_at, end_at)``, and reports back how many beats
    and series it sent. ``fulfilled`` with zero beats is an answer, not a failure: the
    watch recorded no tachogram in that span.
    """

    __tablename__ = "beat_capture_window"
    __table_args__ = (
        CheckConstraint("end_at > start_at", name="ck_beat_capture_window_span"),
        CheckConstraint(
            "status IN ('pending', 'fulfilled', 'cancelled')",
            name="ck_beat_capture_window_status",
        ),
    )

    id: Mapped[PrimaryKey[UUID]]
    user_id: Mapped[Indexed[FKUser]]
    start_at: Mapped[datetime]
    end_at: Mapped[datetime]
    status: Mapped[str] = mapped_column(String(16))
    note: Mapped[str_255 | None]
    fulfilled_at: Mapped[datetime | None]
    beats_reported: Mapped[int | None]
    series_reported: Mapped[int | None]
