"""merge upstream data type coverage into fork

Upstream's ca74b1b24e0a (data_type_coverage table, #1597) descends from b679f6226f99
(data_migration table), and this fork's chain ending at d7a3c9e15b20 (per-account data
source identity) from 5455c3eabde3, the last sync's merge, which already includes
b679f6226f99. That leaves two alembic heads; this joins them.

No schema work of its own, and either order is valid: upstream's revision creates a new
table, data_type_coverage, keyed on user, provider and data type; the fork's revision
rebuilds data_source's unique indexes. Neither touches the other's table.

Generated with ``alembic merge heads`` - see backend/AGENTS.md for why a fork sync
adds a merge revision rather than re-pointing an existing one.

Revision ID: f45ee03f3a3e
Revises: d7a3c9e15b20, ca74b1b24e0a

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "f45ee03f3a3e"
down_revision: Union[str, None] = ("d7a3c9e15b20", "ca74b1b24e0a")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
