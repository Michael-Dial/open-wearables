"""Per-participant beat-to-beat capture: beat_capture_setting and beat_capture_window.

Apple Watch beat-to-beat intervals (HKHeartbeatSeriesSample) stay on the phone until the
SDK reads them, so what to collect has to reach the phone. beat_capture_setting holds a
participant's mode (off / sleep / all_day) and how far back it reaches; no row means the
instance default. beat_capture_window holds spans a researcher asked for, which the
phone fulfils whatever the mode and reports back on.

Two new tables, nothing existing is touched.

Revision ID: 7f1b94e46ff7
Revises: a7d3e9c1b2f4
Create Date: 2026-10-13 14:44:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f1b94e46ff7"
down_revision: Union[str, None] = "a7d3e9c1b2f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "beat_capture_setting",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("lookback_days", sa.Integer(), nullable=False),
        sa.Column("sleep_padding_minutes", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("mode IN ('off', 'sleep', 'all_day')", name="ck_beat_capture_setting_mode"),
        sa.CheckConstraint("lookback_days BETWEEN 1 AND 30", name="ck_beat_capture_setting_lookback"),
        sa.CheckConstraint("sleep_padding_minutes BETWEEN 0 AND 240", name="ck_beat_capture_setting_padding"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "beat_capture_window",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("beats_reported", sa.Integer(), nullable=True),
        sa.Column("series_reported", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'fulfilled', 'cancelled')", name="ck_beat_capture_window_status"),
        sa.CheckConstraint("end_at > start_at", name="ck_beat_capture_window_span"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_beat_capture_window_user_id"), "beat_capture_window", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_beat_capture_window_user_id"), table_name="beat_capture_window")
    op.drop_table("beat_capture_window")
    op.drop_table("beat_capture_setting")
