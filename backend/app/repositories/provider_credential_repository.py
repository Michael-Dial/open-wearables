from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.database import DbSession
from app.models import ProviderCredential


class ProviderCredentialRepository:
    """Provider app settings saved in the dashboard, one row per field."""

    def get_for_provider(self, db: DbSession, provider: str) -> dict[str, ProviderCredential]:
        stmt = select(ProviderCredential).where(ProviderCredential.provider == provider)
        return {row.key: row for row in db.execute(stmt).scalars()}

    def apply(
        self,
        db: DbSession,
        provider: str,
        upserts: dict[str, str],
        deletes: set[str],
        developer_id: UUID | None,
    ) -> None:
        """Save and remove fields in one transaction. ``upserts`` values are already encoded."""
        now = datetime.now(timezone.utc)
        for key, value in upserts.items():
            stmt = (
                insert(ProviderCredential)
                .values(key=key, provider=provider, value=value, updated_at=now, updated_by_id=developer_id)
                .on_conflict_do_update(
                    index_elements=["key"],
                    set_={"value": value, "provider": provider, "updated_at": now, "updated_by_id": developer_id},
                )
            )
            db.execute(stmt)
        if deletes:
            db.execute(delete(ProviderCredential).where(ProviderCredential.key.in_(deletes)))
        db.commit()
