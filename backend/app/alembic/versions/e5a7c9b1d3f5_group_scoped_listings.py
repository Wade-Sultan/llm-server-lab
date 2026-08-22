"""listings can target a part group, not just one part

An eBay listing is a filtered search URL, and a search for "RTX 3090" is right
for every partner board of that chipset — there is nothing board-specific in it
to get wrong. Before this, one had to be entered per pc_parts row, which meant
re-entering the same URL for each board and missing every board added later.

A listing now points at exactly one of five things: a part, or one of the four
groups the catalog already models (gpu_chipsets, psu_groups, ram_groups,
storage_groups — the same groups that carry street_price_cents for the same
reason). The CHECK constraint enforces "exactly one", so a row cannot mean two
things or nothing.

Group columns live here rather than in a listings_by_group table on purpose:
the row-level security added in d4e6f8a0b2c3 is attached to `listings`, so a
group listing is covered by the same policies with no further work. A second
table would have been a second thing to remember to protect, and the one it
would hold is eBay data.

Resolution (commerce/internal/store/store.go) prefers a part's own listing over
its group's, so a specific row still overrides the generic one.

Revision ID: e5a7c9b1d3f5
Revises: d4e6f8a0b2c3
Create Date: 2026-08-21 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "e5a7c9b1d3f5"
down_revision = "d4e6f8a0b2c3"
branch_labels = None
depends_on = None

# column on listings -> group table it points at
GROUP_TARGETS = (
    ("gpu_chipset_id", "gpu_chipsets"),
    ("psu_group_id", "psu_groups"),
    ("ram_group_id", "ram_groups"),
    ("storage_group_id", "storage_groups"),
)

TARGET_COLUMNS = ("part_id", *[c for c, _ in GROUP_TARGETS])

# Exactly one target, never two and never none.
_ONE_TARGET = " + ".join(f"({c} IS NOT NULL)::int" for c in TARGET_COLUMNS) + " = 1"


def upgrade():
    # part_id stops being mandatory, but only in the sense that another target
    # may take its place — the CHECK below still requires one of the five.
    op.alter_column(
        "listings", "part_id", existing_type=UUID(as_uuid=True), nullable=True
    )

    for column, table in GROUP_TARGETS:
        op.add_column("listings", sa.Column(column, UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(
            f"fk_listings_{column}",
            "listings",
            table,
            [column],
            ["id"],
            ondelete="CASCADE",
        )
        # Indexed because the read path filters on these per request: resolving
        # a part's listings looks for its group id in every listing row.
        op.create_index(f"ix_listings_{column}", "listings", [column])

    op.create_check_constraint("ck_listings_one_target", "listings", _ONE_TARGET)


def downgrade():
    # Group listings have no part to fall back to, so they are dropped rather
    # than silently reattached to something they were never about.
    op.execute(sa.text("DELETE FROM listings WHERE part_id IS NULL"))
    op.drop_constraint("ck_listings_one_target", "listings", type_="check")

    for column, _ in GROUP_TARGETS:
        op.drop_index(f"ix_listings_{column}", table_name="listings")
        op.drop_constraint(f"fk_listings_{column}", "listings", type_="foreignkey")
        op.drop_column("listings", column)

    op.alter_column(
        "listings", "part_id", existing_type=UUID(as_uuid=True), nullable=False
    )
