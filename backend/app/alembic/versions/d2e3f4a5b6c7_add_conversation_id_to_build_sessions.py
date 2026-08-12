"""add conversation_id to build_sessions

The Postgres half of the join between the two telemetry systems.

LangSmith holds the conversation, the prompts and the model's reasoning;
build_sessions / module_decisions hold the candidate sets, the chosen parts and
the prices at decision time. Both describe the same runs and, until now, shared
no key — so a low-scoring conversation in LangSmith could not be resolved to the
candidate list that produced it, and a suspect build could not be resolved back
to the conversation that asked for it.

app/core/tracing.py::attach_thread puts the same id into LangSmith run metadata
(as thread_id / session_id / conversation_id, since which one LangSmith groups
Threads on has moved across versions), and BuildRecorder puts build_session_id
there too. That gives a two-way join:

    LangSmith trace --(build_session_id)--> module_decisions
    build_sessions  --(conversation_id)---> LangSmith Thread

NOT A FOREIGN KEY, deliberately. Guest turns have no `conversations` row at all
— chat_pipeline synthesizes a "turn:<uuid>" thread id for them — and a FK would
either reject those rows or force the id to be dropped. Telemetry must never
constrain what the product is allowed to do, and an orphaned id here is inert.

Indexed because the query this exists to serve ("show me every build session for
this conversation") filters on it, and build_sessions grows per build forever.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-08-11 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "build_sessions",
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "ix_build_sessions_conversation_id", "build_sessions", ["conversation_id"]
    )


def downgrade():
    op.drop_index("ix_build_sessions_conversation_id", table_name="build_sessions")
    op.drop_column("build_sessions", "conversation_id")
