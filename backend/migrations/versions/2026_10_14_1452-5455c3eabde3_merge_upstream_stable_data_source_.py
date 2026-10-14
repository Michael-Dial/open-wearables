"""merge upstream stable data source identity into fork

Upstream's chain 2a52f24a8ccf (data source stable identity, #1781) -> 76f7f8b66312
(health_score data_source index) -> b679f6226f99 (data_migration table, #1864) descends
from c4e1a7b93d20, and this fork's chain ending at 40f81f5301c7, the last sync's merge,
already includes c4e1a7b93d20. That leaves two alembic heads; this joins them.

No schema work of its own, and either order is valid: on this fork 2a52f24a8ccf only adds
three nullable columns to data_source (its index changes are d7a3c9e15b20, which follows
this merge), 76f7f8b66312 adds an index on health_score and b679f6226f99 a new table; the
fork's side since c4e1a7b93d20 touches neither.

Generated with ``alembic merge heads`` - see backend/AGENTS.md for why a fork sync
adds a merge revision rather than re-pointing an existing one.

Revision ID: 5455c3eabde3
Revises: 40f81f5301c7, b679f6226f99

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "5455c3eabde3"
down_revision: Union[str, None] = ("40f81f5301c7", "b679f6226f99")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
