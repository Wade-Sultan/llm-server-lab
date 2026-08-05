"""allow a build to hold several GPUs, drives and fans

pc_build_parts carried a table-wide UNIQUE (build_id, role), which made "one
part per role" a schema-level fact. That is right for the CPU, board, cooler,
PSU and case, and wrong for the three roles a server build is actually defined
by: a machine built to serve LLMs hosts several GPUs, splits storage between a
fast boot drive and bulk capacity, and buys fans per case slot.

The constraint was already costing data. admin/src/app/reference-builds
collects multiple GPU chipsets, storage groups and fans, then dropped all but
the first when writing pc_build_parts because a second row could not be
inserted.

Replaces the one constraint with four, so the guarantee is narrowed rather
than removed:
  - singleton roles keep exactly the old uniqueness
  - the same part cannot be listed twice within one role (use quantity)
  - quantity is at least 1
  - quantity is exactly 1 for singleton roles, so a second CPU is not
    expressible as quantity=2 either

Revision ID: b3d5e7f9a1c2
Revises: a2b4c6d8e0f1
Create Date: 2026-08-05 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa

revision = "b3d5e7f9a1c2"
down_revision = "a2b4c6d8e0f1"
branch_labels = None
depends_on = None

# Kept in sync with models.pcbuild.MULTI_INSTANCE_ROLES. Spelled out rather
# than imported: a migration has to describe the schema at the moment it ran,
# and importing the live model would make an old migration change meaning the
# next time that set is edited.
_MULTI_ROLES = "'fan', 'gpu', 'storage'"
_IS_SINGLETON = f"role NOT IN ({_MULTI_ROLES})"
_IS_MULTI = f"role IN ({_MULTI_ROLES})"


def upgrade():
    op.add_column(
        "pc_build_parts",
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
    )

    op.drop_constraint("uq_pc_build_parts_build_role", "pc_build_parts", type_="unique")

    op.create_index(
        "uq_pc_build_parts_singleton_role",
        "pc_build_parts",
        ["build_id", "role"],
        unique=True,
        postgresql_where=sa.text(_IS_SINGLETON),
    )
    # part_id IS NOT NULL: Postgres treats NULLs as distinct in a unique index,
    # and two unfilled slots in a role are not duplicates of one another.
    op.create_index(
        "uq_pc_build_parts_role_part",
        "pc_build_parts",
        ["build_id", "role", "part_id"],
        unique=True,
        postgresql_where=sa.text("part_id IS NOT NULL"),
    )

    op.create_check_constraint(
        "ck_pc_build_parts_quantity_positive", "pc_build_parts", "quantity >= 1"
    )
    op.create_check_constraint(
        "ck_pc_build_parts_singleton_quantity",
        "pc_build_parts",
        f"quantity = 1 OR {_IS_MULTI}",
    )


def downgrade():
    """LOSSY. Restoring UNIQUE (build_id, role) requires there to be one row per
    role, so any build that used the capability this migration added has to give
    those rows up. The oldest row in each (build_id, role) is kept and the rest
    are deleted — a four-GPU build comes back as a one-GPU build.

    Done explicitly rather than left to fail on the constraint: a downgrade that
    errors out halfway leaves the schema in neither state, which is worse than a
    documented, deterministic loss.
    """
    op.execute(
        """
        DELETE FROM pc_build_parts p
        USING pc_build_parts q
        WHERE p.build_id = q.build_id
          AND p.role = q.role
          AND (p.created_at, p.id) > (q.created_at, q.id)
        """
    )

    op.drop_constraint(
        "ck_pc_build_parts_singleton_quantity", "pc_build_parts", type_="check"
    )
    op.drop_constraint(
        "ck_pc_build_parts_quantity_positive", "pc_build_parts", type_="check"
    )
    op.drop_index("uq_pc_build_parts_role_part", table_name="pc_build_parts")
    op.drop_index("uq_pc_build_parts_singleton_role", table_name="pc_build_parts")

    op.create_unique_constraint(
        "uq_pc_build_parts_build_role", "pc_build_parts", ["build_id", "role"]
    )

    op.drop_column("pc_build_parts", "quantity")
