"""merge upstream 0.10 into fork

Upstream's c4e7a1d93b52 (user.is_seed, #1886) descends from ca74b1b24e0a, and this
fork's head f45ee03f3a3e is the last sync's merge, which already includes ca74b1b24e0a.
That leaves two alembic heads; this joins them.

Upstream backfills is_seed from the seed generator's "[SEED:" first-name prefix. The
fork already records seed users as created_via = 'seed' (user attribution), and from
here on derives is_seed from it (UserService.create), so the backfill is widened to
cover any seed user whose name was edited since.

Revision ID: 6e2d9b4a71c8
Revises: f45ee03f3a3e, c4e7a1d93b52

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6e2d9b4a71c8"
down_revision: Union[str, None] = ("f45ee03f3a3e", "c4e7a1d93b52")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""UPDATE "user" SET is_seed = true WHERE created_via = 'seed' AND NOT is_seed""")


def downgrade() -> None:
    # c4e7a1d93b52's downgrade drops the column; nothing to undo here.
    pass
