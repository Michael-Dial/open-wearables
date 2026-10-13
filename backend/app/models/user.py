from uuid import UUID
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, select
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from app.database import BaseDbModel
from app.mappings import FKDeveloper, Indexed, PrimaryKey, Unique, email, str_16, str_100, str_255
from app.models.api_key import ApiKey


class User(BaseDbModel):
    """Data owner model"""

    __table_args__ = (
        CheckConstraint("created_via IN ('api_key', 'portal', 'seed')", name="ck_user_created_via"),
    )

    id: Mapped[PrimaryKey[UUID]]

    first_name: Mapped[str_100 | None]
    last_name: Mapped[str_100 | None]
    email: Mapped[email | None]

    external_user_id: Mapped[Unique[str_255] | None]

    # Which credential created the user (fork). Set from the request's credential, never
    # from the payload, so a client cannot claim to be another app. NULL `created_via` is
    # a user created before attribution was recorded.
    created_via: Mapped[Indexed[str_16] | None]
    created_by_api_key_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("api_key.id", ondelete="SET NULL"), index=True
    )
    created_by_developer_id: Mapped[FKDeveloper | None]
    # The key's name when it created the user, so attribution outlives the key.
    created_by_label: Mapped[str_100 | None]

    # The creating key's name now, so a rename shows; NULL once the key is deleted.
    # A column rather than a relationship so it is loaded with the row and still readable
    # after the user is deleted (DELETE /users returns the deleted user).
    created_by_api_key_name: Mapped[str | None] = column_property(
        select(ApiKey.name).where(ApiKey.id == created_by_api_key_id).correlate_except(ApiKey).scalar_subquery()
    )

    personal_record: Mapped["PersonalRecord | None"] = relationship(
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
    )
