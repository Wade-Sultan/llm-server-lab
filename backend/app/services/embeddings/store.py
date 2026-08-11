"""
store.py
========
Reads and writes the `embeddings` table: the reconcile sweep that keeps vectors
in step with the catalog, and the similarity search that reads them back.

THE SWEEP IS THE WHOLE SYNCHRONIZATION STRATEGY. There is deliberately no
"embed on insert" hook. Parts enter this database from at least three places —
the discovery pipeline, the admin app's Prisma writes, and hand-run SQL — and
only one of them runs through this codebase at all. A creation hook would cover
that one and silently miss the rest, producing a vector set that is wrong in a
way nothing detects. Content-addressed reconciliation covers all three by
construction: anything whose source text does not match its stored hash gets
re-embedded on the next pass, no matter who wrote it or how.

That also makes the sweep safe to run on a schedule and cheap when idle — a
pass over an unchanged catalog issues one SELECT per entity type, hashes in
process, and makes zero API calls.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.ai_catalog import AIModel
from app.models.embeddings import (
    CATALOG_ENTITIES,
    EmbeddedEntity,
    Embedding,
)
from app.models.games_catalog import Game
from app.models.pcparts import (
    CPU,
    Case,
    CPUCooler,
    Fan,
    GPUChipset,
    Motherboard,
    PSUGroup,
    RAMGroup,
    StorageGroup,
)
from app.models.software_catalog import Software
from app.services.embeddings import client
from app.services.embeddings.text import build_text, content_hash

logger = logging.getLogger(__name__)


# entity_type -> the model whose rows it addresses. The single source of truth
# for "what is embeddable"; adding a type means adding a row here, a builder in
# text.py, and a partial index in a migration.
ENTITY_MODELS: dict[EmbeddedEntity, type] = {
    EmbeddedEntity.GAME: Game,
    EmbeddedEntity.SOFTWARE: Software,
    EmbeddedEntity.AI_MODEL: AIModel,
    EmbeddedEntity.CPU: CPU,
    EmbeddedEntity.GPU_CHIPSET: GPUChipset,
    EmbeddedEntity.MOTHERBOARD: Motherboard,
    EmbeddedEntity.CPU_COOLER: CPUCooler,
    EmbeddedEntity.CASE: Case,
    EmbeddedEntity.FAN: Fan,
    EmbeddedEntity.RAM_GROUP: RAMGroup,
    EmbeddedEntity.PSU_GROUP: PSUGroup,
    EmbeddedEntity.STORAGE_GROUP: StorageGroup,
}

# Types whose model subclasses PCPart and therefore carries is_active. The four
# group tables and the three catalog tables do not have it, and filtering on an
# absent column is an error rather than a no-op.
_HAS_IS_ACTIVE = {
    EmbeddedEntity.CPU,
    EmbeddedEntity.MOTHERBOARD,
    EmbeddedEntity.CPU_COOLER,
    EmbeddedEntity.CASE,
    EmbeddedEntity.FAN,
}


@dataclass
class ReconcileStats:
    """What one sweep did. Logged, and returned to the admin trigger."""

    scanned: int = 0
    embedded: int = 0
    unchanged: int = 0
    skipped_empty: int = 0
    failed: int = 0
    orphans_deleted: int = 0
    tokens: int = 0
    per_type: dict[str, int] = field(default_factory=dict)

    def merge(self, other: ReconcileStats) -> None:
        self.scanned += other.scanned
        self.embedded += other.embedded
        self.unchanged += other.unchanged
        self.skipped_empty += other.skipped_empty
        self.failed += other.failed
        self.orphans_deleted += other.orphans_deleted
        self.tokens += other.tokens
        for k, v in other.per_type.items():
            self.per_type[k] = self.per_type.get(k, 0) + v


@dataclass
class SearchHit:
    """One similarity result. `distance` is cosine distance in 0..2."""

    entity_type: str
    entity_id: uuid.UUID
    distance: float
    source_text: str | None

    @property
    def similarity(self) -> float:
        """Cosine similarity in -1..1 — the friendlier direction to threshold on."""
        return 1.0 - self.distance


async def _existing_hashes(
    db: AsyncSession, entity_type: EmbeddedEntity
) -> dict[uuid.UUID, str]:
    """entity_id -> source_hash for everything already embedded of this type.

    Reads no vectors: this is served entirely by ix_embeddings_type_hash, which
    is why an idle sweep is cheap even with the whole catalog embedded.
    """
    rows = await db.execute(
        select(Embedding.entity_id, Embedding.source_hash).where(
            Embedding.entity_type == entity_type.value,
            # A vector from a different model is not comparable to a current
            # one, so a model change makes every row stale by definition — the
            # hash match is irrelevant if the model differs.
            Embedding.model == settings.EMBEDDING_MODEL,
        )
    )
    return {row.entity_id: row.source_hash for row in rows}


async def _upsert(
    db: AsyncSession,
    entity_type: EmbeddedEntity,
    entity_id: uuid.UUID,
    vector: list[float],
    text: str,
    text_hash: str,
) -> None:
    """Insert or replace one vector, keyed on (entity_type, entity_id)."""
    stmt = (
        pg_insert(Embedding)
        .values(
            id=uuid.uuid4(),
            entity_type=entity_type.value,
            entity_id=entity_id,
            embedding=vector,
            model=settings.EMBEDDING_MODEL,
            dims=settings.EMBEDDING_DIMS,
            source_hash=text_hash,
            source_text=text,
        )
        .on_conflict_do_update(
            constraint="uq_embeddings_entity",
            set_={
                "embedding": vector,
                "model": settings.EMBEDDING_MODEL,
                "dims": settings.EMBEDDING_DIMS,
                "source_hash": text_hash,
                "source_text": text,
            },
        )
    )
    await db.execute(stmt)


async def reconcile_type(
    db: AsyncSession,
    entity_type: EmbeddedEntity,
    *,
    limit: int | None = None,
    delete_orphans: bool = True,
) -> ReconcileStats:
    """Bring one entity type's vectors up to date with its rows."""
    stats = ReconcileStats()
    model = ENTITY_MODELS.get(entity_type)
    if model is None:
        return stats

    stmt = select(model)
    if entity_type in _HAS_IS_ACTIVE:
        stmt = stmt.where(model.is_active == True)  # noqa: E712
    entities = list((await db.execute(stmt)).scalars().all())
    stats.scanned = len(entities)

    existing = await _existing_hashes(db, entity_type)

    pending: list[tuple[uuid.UUID, str, str]] = []  # (id, text, hash)
    live_ids: set[uuid.UUID] = set()

    for entity in entities:
        live_ids.add(entity.id)
        text = build_text(entity_type, entity)
        if not text.strip():
            # A row too sparse to describe. Embedding the bare name alone would
            # produce a vector that matches almost any query weakly and nothing
            # strongly, which is worse than having no vector at all.
            stats.skipped_empty += 1
            continue
        text_hash = content_hash(text)
        if existing.get(entity.id) == text_hash:
            stats.unchanged += 1
            continue
        pending.append((entity.id, text, text_hash))
        if limit is not None and len(pending) >= limit:
            break

    if delete_orphans:
        stale_ids = set(existing) - live_ids
        if stale_ids:
            await db.execute(
                delete(Embedding).where(
                    Embedding.entity_type == entity_type.value,
                    Embedding.entity_id.in_(stale_ids),
                )
            )
            stats.orphans_deleted = len(stale_ids)

    if not pending:
        await db.commit()
        return stats

    result = await client.embed_texts([text for _, text, _ in pending])
    stats.tokens = result.total_tokens

    for (entity_id, text, text_hash), vector in zip(
        pending, result.vectors, strict=False
    ):
        if vector is None:
            # Left un-upserted on purpose: with no row written, the next sweep
            # sees it as missing and retries. Writing a placeholder would make
            # the failure permanent and invisible.
            stats.failed += 1
            continue
        await _upsert(db, entity_type, entity_id, vector, text, text_hash)
        stats.embedded += 1

    await db.commit()
    stats.per_type[entity_type.value] = stats.embedded
    return stats


async def reconcile(
    db: AsyncSession,
    entity_types: list[EmbeddedEntity] | None = None,
    *,
    limit_per_type: int | None = None,
) -> ReconcileStats:
    """Sweep every requested entity type. Defaults to all of them."""
    total = ReconcileStats()
    if not client.is_configured():
        logger.warning(
            "OPENAI_API_KEY unset — embedding reconcile skipped entirely. No "
            "database work was done."
        )
        return total

    for entity_type in entity_types or list(ENTITY_MODELS):
        try:
            total.merge(await reconcile_type(db, entity_type, limit=limit_per_type))
        except Exception:
            # One bad type must not abort the sweep — the others are
            # independent, and a schema drift in (say) fans should not stop
            # games from being embedded.
            logger.exception("embedding reconcile failed for %s", entity_type.value)
            await db.rollback()

    logger.info(
        "embedding reconcile: scanned=%d embedded=%d unchanged=%d "
        "skipped_empty=%d failed=%d orphans=%d tokens=%d",
        total.scanned,
        total.embedded,
        total.unchanged,
        total.skipped_empty,
        total.failed,
        total.orphans_deleted,
        total.tokens,
    )
    return total


async def search(
    db: AsyncSession,
    query: str,
    entity_types: list[EmbeddedEntity],
    *,
    limit: int = 5,
    max_distance: float = 0.65,
) -> list[SearchHit]:
    """Nearest entities to `query` among the given types.

    `max_distance` is a cosine-distance cutoff, and it is doing real work: ANN
    search always returns its k nearest neighbours, so without a cutoff a query
    with no genuine match ("asdfgh") still comes back with the k least-unrelated
    rows in the catalog and reads as a confident answer. 0.65 (≈0.35 cosine
    similarity) is loose enough for a misspelled or abbreviated title and tight
    enough to reject prose about something the catalog does not contain.

    Returns [] rather than raising when embeddings are unconfigured or the query
    cannot be embedded — callers treat that as "no matches", the same state as a
    catalog with nothing in it.
    """
    if not entity_types:
        return []
    vector = await client.embed_one(query)
    if vector is None:
        return []

    distance = Embedding.embedding.cosine_distance(vector).label("distance")
    stmt = (
        select(
            Embedding.entity_type,
            Embedding.entity_id,
            Embedding.source_text,
            distance,
        )
        .where(
            Embedding.entity_type.in_([e.value for e in entity_types]),
            Embedding.model == settings.EMBEDDING_MODEL,
        )
        .order_by(distance)
        .limit(limit)
    )
    rows = await db.execute(stmt)
    return [
        SearchHit(
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            distance=float(row.distance),
            source_text=row.source_text,
        )
        for row in rows
        if row.distance is not None and float(row.distance) <= max_distance
    ]


async def search_catalog(
    db: AsyncSession, query: str, *, limit: int = 3, max_distance: float = 0.65
) -> list[SearchHit]:
    """Search games, software and AI models together.

    One query across all three because the user's phrasing rarely says which it
    is — "I want to run Flux" could name a game, an app or a model, and the
    right answer is whichever the catalog actually contains.
    """
    return await search(
        db,
        query,
        sorted(CATALOG_ENTITIES, key=lambda e: e.value),
        limit=limit,
        max_distance=max_distance,
    )
