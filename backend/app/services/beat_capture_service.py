"""Beat-to-beat capture: per-participant settings and researcher-requested windows.

Apple Watch beat-to-beat intervals stay in HealthKit until the iOS SDK reads them, so
every decision here is one the phone carries out on its next sync. The SDK reads
``sdk_config`` before it collects beats and calls ``record_fulfilment`` once it has
uploaded everything a requested window covers.
"""

from datetime import datetime, timezone
from logging import Logger, getLogger
from uuid import UUID

from app.config import settings
from app.database import DbSession
from app.models import BeatCaptureSetting, BeatCaptureWindow
from app.repositories.beat_capture_repository import BeatCaptureSettingRepository, BeatCaptureWindowRepository
from app.schemas.beat_capture import (
    BeatCaptureMode,
    BeatCaptureSettingRead,
    BeatCaptureSettingUpdate,
    BeatCaptureWindowCreate,
    BeatCaptureWindowStatus,
    SDKBeatCaptureConfig,
    SDKBeatCaptureFulfilment,
    SDKBeatCaptureWindow,
)
from app.services.user_service import user_service
from app.utils.exceptions import ResourceNotFoundError, handle_exceptions
from app.utils.structured_logging import log_structured


class BeatCaptureService:
    def __init__(self, log: Logger):
        self.logger = log
        self.settings_repo = BeatCaptureSettingRepository()
        self.window_repo = BeatCaptureWindowRepository()

    @staticmethod
    def _effective(user_id: UUID, stored: BeatCaptureSetting | None) -> BeatCaptureSettingRead:
        if stored is None:
            return BeatCaptureSettingRead(
                user_id=user_id,
                mode=BeatCaptureMode(settings.beat_capture_default_mode),
                lookback_days=settings.beat_capture_default_lookback_days,
                sleep_padding_minutes=settings.beat_capture_default_sleep_padding_minutes,
                is_default=True,
            )
        return BeatCaptureSettingRead(
            user_id=user_id,
            mode=BeatCaptureMode(stored.mode),
            lookback_days=stored.lookback_days,
            sleep_padding_minutes=stored.sleep_padding_minutes,
            is_default=False,
            updated_at=stored.updated_at,
        )

    @handle_exceptions
    def get_setting(self, db_session: DbSession, user_id: UUID) -> BeatCaptureSettingRead:
        user_service.get(db_session, user_id, raise_404=True)
        return self._effective(user_id, self.settings_repo.get(db_session, user_id))

    @handle_exceptions
    def update_setting(
        self, db_session: DbSession, user_id: UUID, update: BeatCaptureSettingUpdate
    ) -> BeatCaptureSettingRead:
        current = self.get_setting(db_session, user_id)
        stored = self.settings_repo.upsert(
            db_session,
            user_id,
            mode=(update.mode or current.mode).value,
            lookback_days=update.lookback_days or current.lookback_days,
            sleep_padding_minutes=(
                update.sleep_padding_minutes
                if update.sleep_padding_minutes is not None
                else current.sleep_padding_minutes
            ),
            updated_at=datetime.now(timezone.utc),
        )
        log_structured(
            self.logger,
            "info",
            "Beat capture setting updated",
            action="beat_capture_setting_updated",
            user_id=str(user_id),
            mode=stored.mode,
            lookback_days=stored.lookback_days,
            sleep_padding_minutes=stored.sleep_padding_minutes,
        )
        return self._effective(user_id, stored)

    @handle_exceptions
    def create_window(
        self, db_session: DbSession, user_id: UUID, payload: BeatCaptureWindowCreate
    ) -> BeatCaptureWindow:
        user_service.get(db_session, user_id, raise_404=True)
        window = self.window_repo.create(
            db_session,
            user_id,
            start_at=payload.start_at,
            end_at=payload.end_at,
            note=payload.note,
            created_at=datetime.now(timezone.utc),
        )
        log_structured(
            self.logger,
            "info",
            "Beat capture window requested",
            action="beat_capture_window_requested",
            user_id=str(user_id),
            window_id=str(window.id),
            start_at=window.start_at.isoformat(),
            end_at=window.end_at.isoformat(),
        )
        return window

    @handle_exceptions
    def list_windows(
        self, db_session: DbSession, user_id: UUID, status: BeatCaptureWindowStatus | None = None
    ) -> list[BeatCaptureWindow]:
        user_service.get(db_session, user_id, raise_404=True)
        return self.window_repo.list_for_user(db_session, user_id, status.value if status else None)

    @handle_exceptions
    def cancel_window(self, db_session: DbSession, user_id: UUID, window_id: UUID) -> BeatCaptureWindow:
        """Cancel a pending window. A fulfilled one is returned unchanged: its beats are already stored."""
        window = self.window_repo.get_for_user(db_session, user_id, window_id)
        if window is None:
            raise ResourceNotFoundError("beat capture window", window_id)
        if window.status == BeatCaptureWindowStatus.PENDING:
            window.status = BeatCaptureWindowStatus.CANCELLED.value
            window = self.window_repo.save(db_session, window)
        return window

    @handle_exceptions
    def sdk_config(self, db_session: DbSession, user_id: UUID) -> SDKBeatCaptureConfig:
        effective = self._effective(user_id, self.settings_repo.get(db_session, user_id))
        pending = self.window_repo.list_for_user(db_session, user_id, BeatCaptureWindowStatus.PENDING.value)
        return SDKBeatCaptureConfig(
            mode=effective.mode,
            lookback_days=effective.lookback_days,
            sleep_padding_minutes=effective.sleep_padding_minutes,
            windows=[SDKBeatCaptureWindow.model_validate(w) for w in pending],
        )

    @handle_exceptions
    def record_fulfilment(
        self, db_session: DbSession, user_id: UUID, window_id: UUID, report: SDKBeatCaptureFulfilment
    ) -> BeatCaptureWindow:
        """Mark a window fulfilled with the phone's counts.

        Idempotent: a repeated report (the phone retrying after a lost response) updates
        the counts. A window cancelled in the meantime stays cancelled.
        """
        window = self.window_repo.get_for_user(db_session, user_id, window_id)
        if window is None:
            raise ResourceNotFoundError("beat capture window", window_id)
        if window.status == BeatCaptureWindowStatus.CANCELLED:
            return window
        window.status = BeatCaptureWindowStatus.FULFILLED.value
        window.fulfilled_at = window.fulfilled_at or datetime.now(timezone.utc)
        window.beats_reported = report.beats
        window.series_reported = report.series
        window = self.window_repo.save(db_session, window)
        log_structured(
            self.logger,
            "info",
            "Beat capture window fulfilled",
            action="beat_capture_window_fulfilled",
            user_id=str(user_id),
            window_id=str(window_id),
            beats=report.beats,
            series=report.series,
        )
        return window


beat_capture_service = BeatCaptureService(log=getLogger(__name__))
