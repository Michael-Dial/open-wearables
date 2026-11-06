"""observation serial number

A manufacturer serial number beside the route-scoped device identifier, so a Suunto
watch's serial (gear.serialNumber, sent on every workout and dropped until now) is kept
with the description it came with. Nullable, no backfill: null is "not said".

Revision ID: 5d2e8a1c7b90
Revises: cba2f797b561

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "5d2e8a1c7b90"
down_revision: Union[str, None] = "cba2f797b561"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("data_source_observation", sa.Column("device_serial_number", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("data_source_observation", "device_serial_number")
