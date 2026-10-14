"""Record which app created each user: user.created_via and the creating credential.

POST /users now records the credential that sent it: an API key (the integration, named
by the key), a developer's portal session, or the seed-data generator. created_by_label
keeps the key's name at the time so the attribution survives the key being deleted; both
foreign keys are SET NULL so deleting a key or a developer never touches a user.

Four nullable columns, no backfill: nothing records who created the existing users, so
they stay NULL, which the API reports as unknown.

Revision ID: 1bc6845b061f
Revises: 7f1b94e46ff7
Create Date: 2026-10-13 19:09:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1bc6845b061f"
down_revision: Union[str, None] = "7f1b94e46ff7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user", sa.Column("created_via", sa.String(length=16), nullable=True))
    op.add_column("user", sa.Column("created_by_api_key_id", sa.UUID(), nullable=True))
    op.add_column("user", sa.Column("created_by_developer_id", sa.UUID(), nullable=True))
    op.add_column("user", sa.Column("created_by_label", sa.String(length=100), nullable=True))
    op.create_check_constraint("ck_user_created_via", "user", "created_via IN ('api_key', 'portal', 'seed')")
    op.create_foreign_key(
        "user_created_by_api_key_id_fkey",
        "user",
        "api_key",
        ["created_by_api_key_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "user_created_by_developer_id_fkey",
        "user",
        "developer",
        ["created_by_developer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_user_created_via"), "user", ["created_via"], unique=False)
    op.create_index(op.f("ix_user_created_by_api_key_id"), "user", ["created_by_api_key_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_created_by_api_key_id"), table_name="user")
    op.drop_index(op.f("ix_user_created_via"), table_name="user")
    op.drop_constraint("user_created_by_developer_id_fkey", "user", type_="foreignkey")
    op.drop_constraint("user_created_by_api_key_id_fkey", "user", type_="foreignkey")
    op.drop_constraint("ck_user_created_via", "user", type_="check")
    op.drop_column("user", "created_by_label")
    op.drop_column("user", "created_by_developer_id")
    op.drop_column("user", "created_by_api_key_id")
    op.drop_column("user", "created_via")
