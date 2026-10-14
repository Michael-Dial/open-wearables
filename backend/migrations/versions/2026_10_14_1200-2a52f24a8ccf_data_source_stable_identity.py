"""data source stable identity

Revision ID: 2a52f24a8ccf
Revises: c4e1a7b93d20

Fork: adds the columns only. Upstream's version adds ``data_source.device_id`` as the
provider's device identifier, a name this fork already uses for the device-registry
foreign key, and rebuilds ``uq_data_source_identity`` without ``user_connection_id``,
which would let two accounts of one provider collide. Here the identifier is
``provider_device_id``, and the identity indexes are rebuilt per account by
``d7a3c9e15b20`` (data source stable identity per account), which runs after this
branch and the fork's own have been joined - it has to, because the fork's
``b5d41c7a9e02`` also rebuilds ``uq_data_source_identity`` and sits on the other branch.

See docs/fork/DECISIONS.md, "Stable data source identity is per account".
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2a52f24a8ccf"
down_revision: Union[str, None] = "c4e1a7b93d20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("data_source", sa.Column("provider_device_id", sa.String(length=100), nullable=True))
    op.add_column("data_source", sa.Column("source_app_id", sa.String(length=100), nullable=True))
    op.add_column("data_source", sa.Column("device_manufacturer", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("data_source", "device_manufacturer")
    op.drop_column("data_source", "source_app_id")
    op.drop_column("data_source", "provider_device_id")
