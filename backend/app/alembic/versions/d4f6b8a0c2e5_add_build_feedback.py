"""add build feedback

One standing thumbs up/down per user per conversation on the build that was
recommended to them.

Two foreign keys rather than one because pc_builds rows are shared templates —
every conversation landing on the same build_key points at the same pc_build id.
conversation_id is therefore the identity, and build_id is denormalized so the
"which template scores worst" question is a group-by rather than a join through
conversations.build_id, which is written once and can drift after a rewind. See
app/models/build_feedback.py.

Revision ID: d4f6b8a0c2e5
Revises: c3e5a7b9d1f4
Create Date: 2026-08-15 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM, UUID

revision = "d4f6b8a0c2e5"
down_revision = "c3e5a7b9d1f4"
branch_labels = None
depends_on = None

# postgresql.ENUM with create_type=False, NOT sa.Enum, and the flag is the whole
# point. A plain sa.Enum column makes create_table emit its own CREATE TYPE
# through SQLAlchemy's before_create hook, which passes checkfirst=False no
# matter what the explicit create below asked for — so the type is created
# twice in the same migration and the second one fails with
# `DuplicateObject: type "feedback_rating" already exists`.
#
# create_type=False detaches the type's lifecycle from the table's, which means
# it must be created and dropped by hand — see upgrade/downgrade. That is also
# what makes this rerunnable against a database where a failed earlier attempt
# left the type behind: checkfirst=True then finds it and moves on.
_RATING = ENUM("up", "down", name="feedback_rating", create_type=False)


def upgrade():
    bind = op.get_bind()
    _RATING.create(bind, checkfirst=True)

    op.create_table(
        "build_feedback",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SET NULL, not CASCADE: retiring a build template must not delete the
        # record that someone disliked it.
        sa.Column(
            "build_id",
            UUID(as_uuid=True),
            sa.ForeignKey("pc_builds.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rating", _RATING, nullable=False),
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
        sa.UniqueConstraint(
            "conversation_id",
            "user_id",
            name="uq_build_feedback_conversation_user",
        ),
    )

    op.create_index("ix_build_feedback_id", "build_feedback", ["id"])
    op.create_index(
        "ix_build_feedback_conversation_id", "build_feedback", ["conversation_id"]
    )
    op.create_index("ix_build_feedback_build_id", "build_feedback", ["build_id"])
    op.create_index("ix_build_feedback_user_id", "build_feedback", ["user_id"])


def downgrade():
    op.drop_index("ix_build_feedback_user_id", table_name="build_feedback")
    op.drop_index("ix_build_feedback_build_id", table_name="build_feedback")
    op.drop_index("ix_build_feedback_conversation_id", table_name="build_feedback")
    op.drop_index("ix_build_feedback_id", table_name="build_feedback")
    op.drop_table("build_feedback")
    _RATING.drop(op.get_bind(), checkfirst=True)
