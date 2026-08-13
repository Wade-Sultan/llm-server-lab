"""add price subscriptions

Creates price_subscriptions (one row per user watching one part) and
price_subscription_targets (per-target subscriber counts, recomputed from the
rows above rather than incremented). Also adds pricing_runs.alerts_sent, since
the pricing ETL is what evaluates and fires these.

Targets are the same (target_kind, target_id) pair price_checks uses rather
than an FK to pc_parts: street_price_cents lives on pc_parts for some part
types and on the group tables for the rest, so a subscription must be able to
point at either.

Revision ID: b8d2f4a6c1e3
Revises: f4a5b6c7d8e9
Create Date: 2026-08-12 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "b8d2f4a6c1e3"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "price_subscriptions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, index=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("target_kind", sa.Text(), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=False),
        sa.Column("threshold_cents", sa.Integer(), nullable=True),
        sa.Column("baseline_price_cents", sa.Integer(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notified_price_cents", sa.Integer(), nullable=True),
        sa.Column("notify_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # The pricing ETL's read after it applies a new price: "who is watching
    # this target". Partial because a sent or canceled row is never read again.
    op.create_index(
        "ix_price_subscriptions_active_target",
        "price_subscriptions",
        ["target_kind", "target_id"],
        postgresql_where=sa.text("status = 'active'"),
    )
    # One live subscription per user per target, but the same user may
    # re-subscribe after being alerted — hence partial rather than a plain
    # unique constraint, which would make the sent row block the new one.
    op.create_index(
        "uq_price_subscriptions_active_user_target",
        "price_subscriptions",
        ["user_id", "target_kind", "target_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "price_subscription_targets",
        sa.Column("target_kind", sa.Text(), primary_key=True),
        sa.Column("target_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("active_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.add_column(
        "pricing_runs",
        sa.Column("alerts_sent", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("pricing_runs", "alerts_sent")
    op.drop_table("price_subscription_targets")
    op.drop_index(
        "uq_price_subscriptions_active_user_target", table_name="price_subscriptions"
    )
    op.drop_index(
        "ix_price_subscriptions_active_target", table_name="price_subscriptions"
    )
    op.drop_table("price_subscriptions")
