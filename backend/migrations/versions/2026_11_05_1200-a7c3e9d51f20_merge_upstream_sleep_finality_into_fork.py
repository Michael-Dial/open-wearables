"""Merge upstream's sleep_details.provider_state (#2036) into the fork's head.

Upstream added 3f6b9c2d8e41 on top of e9413ac05e3b; the fork's head is ffae1258889c (provider
credentials). The two touch different tables, so this only joins the heads.

Revision ID: a7c3e9d51f20
Revises: ffae1258889c, 3f6b9c2d8e41
Create Date: 2026-11-05 12:00:00.000000

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "a7c3e9d51f20"
down_revision: Union[str, Sequence[str], None] = ("ffae1258889c", "3f6b9c2d8e41")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
