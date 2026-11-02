"""merge upstream aggregates into fork

Upstream's 6c49f0f8fba2..e9413ac05e3b chain (series_sample and the aggregate tables,
series_sample_backup, per-source health score key, on-demand history rollup) and this
fork's merge point 6e2d9b4a71c8 both descend from c4e7a1d93b52, leaving two alembic heads
after the sync. This joins them so there is a single head again.

Order: upstream's chain renames data_point_series to series_sample, and the fork revisions
b4e8f2a7c915 and a7d3e9c1b2f4 add columns to data_point_series. On an existing database
those already ran. On an empty one alembic runs them before the rename (they sit before
6e2d9b4a71c8, which shares its branch point with the rename); a fresh upgrade was checked.

One row of data: upstream's startup script fix_polar_workout_utc_offset moves Polar
exercises stored twice their UTC offset away to their real time. The fork fixed the same
bug in 2026-09 and retimed the rows already stored, with their RR beats, in c6d2a8f4e1b3.
Upstream's script would move every exercise it cannot re-read from Polar by twice its
offset a second time, and leave its RR beats behind, so it is recorded here as completed:
data_migration_service skips a completed script, whether started at startup or by hand.

Revision ID: d8b3f1a6c2e4
Revises: 6e2d9b4a71c8, e9413ac05e3b

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8b3f1a6c2e4"
down_revision: Union[str, None] = ("6e2d9b4a71c8", "e9413ac05e3b")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO data_migration (name, created_at) VALUES ('fix_polar_workout_utc_offset', now()) "
        "ON CONFLICT (name) DO NOTHING"
    )


def downgrade() -> None:
    op.execute("DELETE FROM data_migration WHERE name = 'fix_polar_workout_utc_offset'")
