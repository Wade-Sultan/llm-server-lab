"""ensure pc_parts pricing columns exist (repair schema drift)

The pricing columns were introduced in 1d9f3b7c2a44, but some databases have
that revision marked applied in alembic_version without the columns actually
present (schema drift) — the polymorphic PCPart load then fails with
'column pc_parts.msrp_cents does not exist'. Because Alembic considers
1d9f3b7c2a44 applied, it won't re-run it, so this migration idempotently
re-adds any missing pricing column. It's a no-op on a correctly-migrated DB.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-13 00:00:00.000000

"""
from alembic import op

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE pc_parts ADD COLUMN IF NOT EXISTS msrp_cents integer")
    op.execute("ALTER TABLE pc_parts ADD COLUMN IF NOT EXISTS street_price_cents integer")
    op.execute("ALTER TABLE pc_parts ADD COLUMN IF NOT EXISTS price_source varchar(20)")
    op.execute(
        "ALTER TABLE pc_parts ADD COLUMN IF NOT EXISTS used_market_viable boolean DEFAULT false"
    )


def downgrade():
    # No-op: these columns are logically owned by 1d9f3b7c2a44; dropping them
    # here would fight that migration's downgrade. This revision only repairs
    # drift, so it has nothing of its own to undo.
    pass
