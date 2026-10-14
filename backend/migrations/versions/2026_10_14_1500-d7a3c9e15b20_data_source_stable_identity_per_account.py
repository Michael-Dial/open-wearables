"""data source stable identity per account

Upstream #1781's identity tiers, with the account in every one of them. A data source is
matched on the provider's device id when it has one, else on the writing app's id and
the model, else on model + source - and each tier is unique per user, provider and
account. Upstream's version of these indexes leaves the account out, which would put two
units a participant wears on two accounts of one provider onto one data source.

Replaces the fork's uq_data_source_identity (from b5d41c7a9e02) with the same columns,
now partial: it only covers rows without stable ids, which the other two tiers own.

The downgrade restores the non-partial index. Rows that differ only by their stable ids
would collide under it, so their source is suffixed first, as upstream's downgrade does.

Revision ID: d7a3c9e15b20
Revises: 5455c3eabde3

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7a3c9e15b20"
down_revision: Union[str, None] = "5455c3eabde3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ACCOUNT = sa.literal_column("COALESCE(user_connection_id, '00000000-0000-0000-0000-000000000000'::uuid)")
MODEL = sa.literal_column("COALESCE(device_model, '')")
SOURCE = sa.literal_column("COALESCE(source, '')")


def upgrade() -> None:
    op.create_index(
        "uq_data_source_device",
        "data_source",
        ["user_id", "provider", "provider_device_id", ACCOUNT],
        unique=True,
        postgresql_where=sa.text("provider_device_id IS NOT NULL"),
    )
    op.create_index(
        "uq_data_source_app",
        "data_source",
        ["user_id", "provider", "source_app_id", MODEL, ACCOUNT],
        unique=True,
        postgresql_where=sa.text("provider_device_id IS NULL AND source_app_id IS NOT NULL"),
    )
    op.drop_index("uq_data_source_identity", table_name="data_source")
    op.create_index(
        "uq_data_source_identity",
        "data_source",
        ["user_id", "provider", MODEL, SOURCE, ACCOUNT],
        unique=True,
        postgresql_where=sa.text("provider_device_id IS NULL AND source_app_id IS NULL"),
    )


def downgrade() -> None:
    op.execute("""
        UPDATE data_source d SET source = left(coalesce(d.source, ''), 90) || ' #' || left(d.id::text, 8)
        WHERE (d.provider_device_id IS NOT NULL OR d.source_app_id IS NOT NULL)
          AND EXISTS (
              SELECT 1 FROM data_source o
              WHERE o.id <> d.id AND o.user_id = d.user_id AND o.provider = d.provider
                AND coalesce(o.user_connection_id, '00000000-0000-0000-0000-000000000000'::uuid)
                    = coalesce(d.user_connection_id, '00000000-0000-0000-0000-000000000000'::uuid)
                AND coalesce(o.device_model, '') = coalesce(d.device_model, '')
                AND coalesce(o.source, '') = coalesce(d.source, '')
          )
    """)
    op.drop_index("uq_data_source_identity", table_name="data_source")
    op.create_index(
        "uq_data_source_identity",
        "data_source",
        ["user_id", "provider", MODEL, SOURCE, ACCOUNT],
        unique=True,
    )
    op.drop_index("uq_data_source_app", table_name="data_source")
    op.drop_index("uq_data_source_device", table_name="data_source")
