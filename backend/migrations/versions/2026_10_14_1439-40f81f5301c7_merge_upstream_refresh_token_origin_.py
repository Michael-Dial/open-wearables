"""merge upstream refresh token origin into fork

Upstream"s c4e1a7b93d20 (refresh_token origin, #1841) descends from 4b9d28928ca1, and this
fork"s chain ending at 1bc6845b061f (user creation attribution) from f4c24463e549, the
last sync"s merge, which already includes 4b9d28928ca1. That leaves two alembic heads;
this joins them.

No schema work of its own, and either order is valid: upstream"s revision adds columns to
refresh_token; the fork"s adds columns to "user". Neither touches the other"s table.

Generated with ``alembic merge heads`` - see backend/AGENTS.md for why a fork sync
adds a merge revision rather than re-pointing an existing one.

Revision ID: 40f81f5301c7
Revises: c4e1a7b93d20, 1bc6845b061f

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "40f81f5301c7"
down_revision: Union[str, None] = ("c4e1a7b93d20", "1bc6845b061f")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
