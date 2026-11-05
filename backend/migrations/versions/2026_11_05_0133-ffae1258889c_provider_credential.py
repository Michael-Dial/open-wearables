"""provider credential

Provider app settings (client ID and secret, redirect URI, scopes, webhook tokens) saved
from the dashboard. One row per field; a field with no row falls back to the environment,
so an existing deployment is unchanged until someone saves a value.

Revision ID: ffae1258889c
Revises: d8b3f1a6c2e4

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "ffae1258889c"
down_revision: Union[str, None] = "d8b3f1a6c2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "provider_credential",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["updated_by_id"], ["developer.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(op.f("ix_provider_credential_provider"), "provider_credential", ["provider"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_provider_credential_provider"), table_name="provider_credential")
    op.drop_table("provider_credential")
