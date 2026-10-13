from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert

from app.database import DbSession
from app.models import BeatCaptureSetting, BeatCaptureWindow


class BeatCaptureSettingRepository:
    def get(self, db_session: DbSession, user_id: UUID) -> BeatCaptureSetting | None:
        return db_session.get(BeatCaptureSetting, user_id)

    def upsert(
        self,
        db_session: DbSession,
        user_id: UUID,
        mode: str,
        lookback_days: int,
        sleep_padding_minutes: int,
        updated_at: datetime,
    ) -> BeatCaptureSetting:
        values = {
            "mode": mode,
            "lookback_days": lookback_days,
            "sleep_padding_minutes": sleep_padding_minutes,
            "updated_at": updated_at,
        }
        stmt = insert(BeatCaptureSetting).values(user_id=user_id, **values)
        db_session.execute(stmt.on_conflict_do_update(index_elements=["user_id"], set_=values))
        db_session.commit()
        setting = db_session.get(BeatCaptureSetting, user_id, populate_existing=True)
        assert setting is not None
        return setting


class BeatCaptureWindowRepository:
    def create(
        self,
        db_session: DbSession,
        user_id: UUID,
        start_at: datetime,
        end_at: datetime,
        note: str | None,
        created_at: datetime,
    ) -> BeatCaptureWindow:
        window = BeatCaptureWindow(
            id=uuid4(),
            user_id=user_id,
            start_at=start_at,
            end_at=end_at,
            status="pending",
            note=note,
            created_at=created_at,
        )
        db_session.add(window)
        db_session.commit()
        db_session.refresh(window)
        return window

    def get_for_user(self, db_session: DbSession, user_id: UUID, window_id: UUID) -> BeatCaptureWindow | None:
        return (
            db_session.query(BeatCaptureWindow)
            .filter(BeatCaptureWindow.id == window_id, BeatCaptureWindow.user_id == user_id)
            .one_or_none()
        )

    def list_for_user(self, db_session: DbSession, user_id: UUID, status: str | None = None) -> list[BeatCaptureWindow]:
        query = db_session.query(BeatCaptureWindow).filter(BeatCaptureWindow.user_id == user_id)
        if status is not None:
            query = query.filter(BeatCaptureWindow.status == status)
        return query.order_by(BeatCaptureWindow.start_at, BeatCaptureWindow.created_at).all()

    def save(self, db_session: DbSession, window: BeatCaptureWindow) -> BeatCaptureWindow:
        db_session.add(window)
        db_session.commit()
        db_session.refresh(window)
        return window
