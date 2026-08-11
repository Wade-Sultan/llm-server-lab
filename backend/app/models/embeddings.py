"""
embeddings.py
=============
One table holding every vector in the system — catalog entities (games,
software, AI models) and parts alike.

WHY ONE TABLE RATHER THAN ONE PER TYPE. A `cpu_vectors` / `gpu_vectors` /
`game_vectors` split gives identical query plans to this once a partial index
per type exists (see below), and costs a nine-file Alembic revision every time
the embedding model or its dimension count changes. Embedding models get
swapped far more often than parts schemas do, so the axis worth optimizing for
is "re-embed everything cheaply", not "keep each type's rows apart".

WHY THERE IS NO FOREIGN KEY. `entity_id` addresses nine different tables —
pc_parts subclasses, the four group tables (which are NOT under pc_parts), and
three catalog tables. Postgres has no FK that can target a union of tables, so
referential integrity here is maintained by the sweep in
app/services/embeddings/store.py rather than by the database. That sweep deletes
rows whose entity has disappeared; until it runs, an orphan is inert — searches
join back to the source table and a missing join simply drops the row.

WHY EMBEDDINGS SIT ON GROUPS, NOT EXACTS. For GPU/RAM/PSU/Storage the intrinsic
spec lives on the group and the exacts differ only in brand, price and physical
dimensions — none of which carry semantic meaning worth a vector. Embedding
every board of an RTX 5080 would store twenty near-identical vectors that all
match the same query and then have to be deduped back down to the chipset. So
`entity_type` for those is the group ('gpu_chipset', 'ram_group', ...), matching
how the recommender already reasons about them.

STALENESS IS CONTENT-ADDRESSED. `source_hash` is a SHA-256 of the exact text
that produced the vector. Re-embedding is therefore a pure function of the row's
current content: build the text, hash it, compare. That is what lets the
reconcile sweep pick up newly-inserted parts and edited rows without a creation
hook in every write path — including the admin app's Prisma writes, which the
backend never sees.
"""

from __future__ import annotations

import enum
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.db.base import Base

# Dimension of the stored vectors. Fixed in the column type because pgvector
# requires it, so changing embedding models to one with a different width is a
# migration (ALTER the column, re-embed everything) rather than a config flip.
# 1536 is text-embedding-3-small's native width and sits comfortably under
# pgvector's 2000-dimension ceiling for HNSW indexes.
EMBEDDING_DIMS = 1536


class EmbeddedEntity(str, enum.Enum):
    """What an embedding row points at.

    Values are stable strings written into `embeddings.entity_type` and used as
    the partial-index predicate, so renaming one is a migration.
    """

    # --- Catalog: the query side. These are what free-text user input is
    # matched against ("I play Arc Raiders", "I edit in Resolve").
    GAME = "game"
    SOFTWARE = "software"
    AI_MODEL = "ai_model"

    # --- Parts. Groups where the type has them, the part itself otherwise.
    CPU = "cpu"
    GPU_CHIPSET = "gpu_chipset"
    MOTHERBOARD = "motherboard"
    CPU_COOLER = "cpu_cooler"
    CASE = "case"
    FAN = "fan"
    RAM_GROUP = "ram_group"
    PSU_GROUP = "psu_group"
    STORAGE_GROUP = "storage_group"


# Entity types that describe user intent rather than hardware. Kept as a set
# because catalog matching searches across all three at once and parts search
# never wants them mixed in.
CATALOG_ENTITIES = frozenset(
    {EmbeddedEntity.GAME, EmbeddedEntity.SOFTWARE, EmbeddedEntity.AI_MODEL}
)

PART_ENTITIES = frozenset(EmbeddedEntity) - CATALOG_ENTITIES


class Embedding(Base):
    __tablename__ = "embeddings"

    __table_args__ = (
        # One vector per entity. The upsert in store.py targets this constraint,
        # so re-embedding an edited row replaces rather than accumulates.
        UniqueConstraint("entity_type", "entity_id", name="uq_embeddings_entity"),
        # Covers the reconcile sweep's "which of these entities are already
        # embedded, and at what hash" lookup, which reads no vector at all.
        Index("ix_embeddings_type_hash", "entity_type", "source_hash"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

    entity_type = Column(
        String(32),
        nullable=False,
        doc="EmbeddedEntity value — also the partial-index predicate",
    )
    entity_id = Column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
        doc="PK in the table named by entity_type. Deliberately not an FK — see "
        "the module docstring",
    )

    embedding = Column(Vector(EMBEDDING_DIMS), nullable=False)

    model = Column(
        String(64),
        nullable=False,
        doc="Embedding model that produced this vector, e.g. "
        "'text-embedding-3-small'. Vectors from different models are not "
        "comparable, so a search must never mix them",
    )
    dims = Column(
        Integer,
        nullable=False,
        doc="Width actually written. Redundant with the column type today, and "
        "the thing that makes a mid-migration mixed state detectable",
    )

    source_hash = Column(
        String(64),
        nullable=False,
        doc="SHA-256 of source_text. Re-embed exactly when this changes",
    )
    source_text = Column(
        Text,
        nullable=True,
        doc="The text that was embedded. Kept for debugging a bad match — "
        "without it, a wrong result is unattributable",
    )

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
