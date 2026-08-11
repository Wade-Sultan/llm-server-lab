"""enable pgvector and add the unified embeddings table

Adds semantic search over two things:

  * The catalog side — games, software and AI models. This is the half that
    earns its keep: BuildProfile.games / .workloads arrive as free text
    ("Arc Raiders", "DaVinci Resolve", "Llama 70B") with unbounded vocabulary,
    misspellings and abbreviations, and until now reached the pipeline only as
    prose inside a prompt. Matching them to catalog rows attaches the
    requirement data those rows carry (game_minimum_parts, software_tiers,
    ai_workloads), none of which the recommender previously read.
  * The parts side — one vector per part or part *group*, for text search over
    the catalog ("quiet white ITX case").

ONE TABLE, PARTIAL INDEXES. Every vector lives in `embeddings`, discriminated
by entity_type. HNSW cannot filter efficiently inside a single shared index —
it would return k nearest overall and then throw away everything of the wrong
type — so each entity_type gets its own partial HNSW index. That gives the same
plan a per-type table would, while keeping a model swap to one ALTER.

COSINE, NOT L2. text-embedding-3-small returns normalized vectors, where cosine
distance and L2 rank identically; cosine is chosen because it stays correct if a
future model returns unnormalized output, and because its 0..2 range makes
distance thresholds portable across models.

Revision ID: c1d2e3f4a5b6
Revises: b2d4f6a8c0e1
Create Date: 2026-08-11 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import UUID

revision = "c1d2e3f4a5b6"
down_revision = "b2d4f6a8c0e1"
branch_labels = None
depends_on = None


# Must match app.models.embeddings.EMBEDDING_DIMS.
_DIMS = 1536

# Must match app.models.embeddings.EmbeddedEntity. Each gets its own partial
# HNSW index because every search is scoped to exactly one of them.
_ENTITY_TYPES = [
    "game",
    "software",
    "ai_model",
    "cpu",
    "gpu_chipset",
    "motherboard",
    "cpu_cooler",
    "case",
    "fan",
    "ram_group",
    "psu_group",
    "storage_group",
]


def upgrade():
    # Cloud SQL for PostgreSQL ships pgvector; the extension still has to be
    # created per-database. Requires the cloudsqlsuperuser role, which the
    # migration user has.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "embeddings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, index=True),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=False),
        sa.Column("embedding", Vector(_DIMS), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("dims", sa.Integer(), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=True),
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

    op.create_unique_constraint(
        "uq_embeddings_entity", "embeddings", ["entity_type", "entity_id"]
    )
    op.create_index("ix_embeddings_entity_id", "embeddings", ["entity_id"])
    # Serves the reconcile sweep, which reads (entity_type, source_hash) to
    # decide what needs re-embedding and touches no vector at all.
    op.create_index(
        "ix_embeddings_type_hash", "embeddings", ["entity_type", "source_hash"]
    )

    # m/ef_construction are pgvector's defaults (16 / 64). At the scale here —
    # thousands of rows per type, not millions — the defaults are already well
    # past the point of diminishing returns, and raising them would only slow
    # the backfill down for recall that is already effectively exact.
    for entity_type in _ENTITY_TYPES:
        op.execute(
            f"CREATE INDEX ix_embeddings_hnsw_{entity_type} "
            f"ON embeddings USING hnsw (embedding vector_cosine_ops) "
            f"WHERE entity_type = '{entity_type}'"
        )


def downgrade():
    for entity_type in _ENTITY_TYPES:
        op.execute(f"DROP INDEX IF EXISTS ix_embeddings_hnsw_{entity_type}")
    op.drop_index("ix_embeddings_type_hash", table_name="embeddings")
    op.drop_index("ix_embeddings_entity_id", table_name="embeddings")
    op.drop_constraint("uq_embeddings_entity", "embeddings", type_="unique")
    op.drop_table("embeddings")
    # The extension is deliberately left in place. Dropping it would break any
    # other database object built on the vector type, and re-creating it is
    # free — whereas a DROP EXTENSION CASCADE here would be silently
    # destructive.
