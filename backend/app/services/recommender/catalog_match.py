"""
catalog_match.py
================
Resolves the free text a user gives us — game titles, app names, model names —
to rows in the games / software / ai_models catalogs, and pulls the hardware
requirements those rows carry into the build pipeline.

THE GAP THIS CLOSES. `BuildProfile.games` and `.workloads` are captured as free
text and, until now, reached the recommender only as prose inside
`use_case_summary`. Meanwhile `game_minimum_parts`, `software_tiers` and
`ai_workloads` sat in the database holding exactly the numbers those titles
imply — minimum VRAM, recommended RAM, storage floors — and nothing read them.
So a user who said "I want to run Llama 3.1 70B" got a build chosen by a model
reasoning about the words "Llama 70B", when the catalog knew the workload needs
40GB+ of VRAM and multi-GPU sharding.

TWO-TIER MATCHING, and the order matters.

  1. Exact name or curated alias (`_match_by_alias`). A synonym someone typed
     into the admin panel is a *fact*: "R6" IS Rainbow Six Siege. Resolving a
     known fact by approximate nearest neighbour is both slower (an embedding
     API call) and less reliable — a two-character query has to out-compete
     every other row's prose to clear the distance cutoff, and often will not.
  2. Vector search, for everything aliases do not cover: misspellings, loose
     sequel names, phrasings nobody thought to curate. This is the unbounded
     tail, and it is the retrieval problem embeddings are genuinely good at —
     unlike ranking parts by price and performance, which is arithmetic and
     lives in scoring.py.

Aliases also go into the embedded source text, so tier 2 benefits from them
too: "r6 siege" misses the exact match but lands much closer to a row whose
text now contains "Also known as R6, Siege".

EVERYTHING HERE DEGRADES TO EMPTY. No API key, no vectors backfilled, no match
above threshold — all produce `CatalogRequirements()` with nothing set, and the
pipeline runs exactly as it did before. Requirements are additive context, never
a precondition.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.ai_catalog import AIModel
from app.models.embeddings import EmbeddedEntity
from app.models.games_catalog import Game, RequirementTier
from app.models.software_catalog import Software
from app.services.embeddings import store

logger = logging.getLogger(__name__)

# Distance cutoff for accepting a catalog match. Tighter than store.search's
# default because a wrong match here is worse than no match: it attaches real
# numeric requirements to the build under a name the user never said, and those
# numbers then look authoritative. A missed match only costs the enrichment.
_MATCH_MAX_DISTANCE = 0.45

# Cap on how many free-text terms are resolved per build. Each is one embedding
# call, and a profile listing fifteen games is describing a general gaming
# machine rather than fifteen sets of requirements to satisfy simultaneously.
_MAX_TERMS = 6

# Handed to the build steps whenever an LLM the catalog has no row for comes up.
#
# WHY THIS IS PROSE AND NOT A LOOKUP. ai_workloads already stores exactly these
# numbers, curated, for every model we know — and the whole problem is the
# models we don't. A name is all we have there, and the parameter count inside
# it is enough to do the arithmetic, so the arithmetic is what gets shipped.
#
# The failure this exists to stop: a user asked for a single-GPU box serving a
# 31B model and got a $15,000 96GB workstation card, because nothing in the
# pipeline said that weights shrink with precision. At q4 that model is ~19GB
# and runs on a 24GB consumer card — a difference between two builds an order
# of magnitude apart in price, turning entirely on a fact no step was told.
_QUANTIZATION_PRIMER = """\
  Quantization decides how much VRAM an LLM needs, and it is not optional
  context — the same model spans a ~4x VRAM range across the formats people
  actually run, so a VRAM figure quoted without a precision is meaningless.
  Estimate weights as: VRAM_GB ~= params_billions * bytes_per_weight * 1.2
  (the 1.2 covers KV cache and runtime overhead at typical context lengths).
  bytes_per_weight: fp16/bf16 = 2.0, fp8/int8 = 1.0, q6 = 0.75, q5 = 0.65,
  q4 = 0.5, q3 = 0.4.
  Self-hosters serving a model locally run q4-q8 as a matter of course; 4-bit
  is the default assumption for a single-GPU box unless the user asked for full
  precision. Quote the precision alongside the card, and prefer the cheapest
  card that fits the model at a sane quantization over a larger card that fits
  it at fp16 — the quality cost of q4 vs fp16 is small and the price
  difference is not. Only size for fp16 when the user asked for it, when the
  workload is training rather than serving, or when no quantized format exists
  for that architecture."""

# Profile ai_workload values -> the AITask rows worth reading, best first.
# "training" maps to LoRA ahead of a full fine-tune because it is what someone
# training on a desktop is realistically doing; full-weight training numbers
# would size the build for a workload almost no consumer build runs.
_AI_TASK_PREFERENCE = {
    "inference": ("inference",),
    "training": ("fine_tune_lora", "fine_tune_qlora", "fine_tune_full"),
    "image_gen": ("inference",),
}


@dataclass
class CatalogRequirements:
    """Aggregated hardware floors implied by everything the user named.

    Floors are maxima across matches: a build that must run both Cyberpunk and
    Resolve needs the larger of the two RAM figures, not their average. Every
    field is optional — an absent floor means the catalog had nothing to say,
    which is different from a floor of zero.
    """

    matched_names: list[str] = field(default_factory=list)
    unmatched_terms: list[str] = field(default_factory=list)

    min_vram_gb: int | None = None
    min_ram_gb: int | None = None
    min_storage_gb: int | None = None
    min_cores: int | None = None

    # True when any matched AI workload declares it shards across cards. This is
    # the one signal that legitimately pushes a build toward multiple GPUs.
    supports_multi_gpu: bool = False
    # True when the catalog holds more than one precision for a matched
    # workload's task, i.e. the VRAM floor below is a function of a choice the
    # build is free to make rather than a property of the model.
    quantization_alternatives: bool = False
    # GPU stacks the matched AI workloads can run on ("cuda" -> nvidia).
    gpu_backends: set[str] = field(default_factory=set)
    # Feature floors from ai_workloads.required_gpu_features / game hard_requirements.
    required_features: set[str] = field(default_factory=set)

    # One line per match, for the prompt.
    notes: list[str] = field(default_factory=list)

    def _raise_floor(self, attr: str, value: int | None) -> None:
        if value is None:
            return
        current = getattr(self, attr)
        if current is None or value > current:
            setattr(self, attr, value)

    @property
    def is_empty(self) -> bool:
        return not self.matched_names and not self.unmatched_terms

    def summary(self) -> str:
        """Prose block appended to the pipeline's use-case summary.

        Written as requirements rather than as a data dump because it lands in a
        prompt read by a 31B model: "Needs at least 24GB VRAM" is actionable,
        `{"min_vram_gb": 24}` invites the model to reformat rather than reason.
        """
        if self.is_empty:
            return ""
        lines: list[str] = []
        if self.matched_names:
            lines.append(
                "Catalog requirements for what the user named "
                f"({', '.join(self.matched_names)}):"
            )
            lines.extend(f"  - {note}" for note in self.notes)

        # NAMING SOMETHING WE HAVE NO ROW FOR IS INFORMATION, not an absence of
        # it, and dropping it silently is how a build gets sized for a model
        # nobody reasoned about. The catalog is always behind — a model released
        # last week has no row and still has to be built for — so an unmatched
        # term is the normal case for exactly the workloads that most need the
        # VRAM arithmetic spelled out. Say what we don't know, and say what to
        # do about it, rather than letting the step infer from silence that the
        # user asked for nothing in particular.
        if self.unmatched_terms:
            lines.append(
                "The user also named the following, which are NOT in our catalog "
                f"— we have no measured requirements for them: "
                f"{', '.join(self.unmatched_terms)}."
            )
            lines.append(
                "  Size the build from the name itself rather than ignoring it. "
                "For an LLM, the parameter count in the name is the input to the "
                "arithmetic below; do not assume it needs no VRAM just because "
                "no row exists for it."
            )
            lines.append(_QUANTIZATION_PRIMER)

        floors = []
        if self.min_vram_gb:
            floors.append(f"at least {self.min_vram_gb}GB of VRAM")
        if self.min_ram_gb:
            floors.append(f"at least {self.min_ram_gb}GB of system RAM")
        if self.min_storage_gb:
            floors.append(f"at least {self.min_storage_gb}GB of storage")
        if self.min_cores:
            floors.append(f"at least {self.min_cores} CPU cores")
        if floors:
            lines.append(f"  Combined floor: {', '.join(floors)}.")
        if self.supports_multi_gpu:
            lines.append(
                "  At least one named workload shards across multiple GPUs, so "
                "total VRAM across cards is what matters for it."
            )
        if self.required_features:
            lines.append(
                f"  Required GPU features: {', '.join(sorted(self.required_features))}."
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """JSONB-safe snapshot, stored on module_decisions.catalog_requirements.

        WHY THIS IS PERSISTED AT ALL. The appropriateness metrics measure
        sufficiency against these floors, and they are recomputed per build from
        catalogs and embeddings that keep changing — so a metric run months
        later against today's catalog would be scoring the decision by a
        yardstick the model never saw. Snapshotting them alongside the candidate
        set is what makes the score reproducible, for exactly the reason
        module_decisions already snapshots candidates verbatim.

        Sets become sorted lists: sets are not JSON-serializable, and sorting
        makes two equal requirement snapshots compare equal as stored JSON.
        """
        return {
            "matched_names": list(self.matched_names),
            "unmatched_terms": list(self.unmatched_terms),
            "min_vram_gb": self.min_vram_gb,
            "min_ram_gb": self.min_ram_gb,
            "min_storage_gb": self.min_storage_gb,
            "min_cores": self.min_cores,
            "supports_multi_gpu": self.supports_multi_gpu,
            "quantization_alternatives": self.quantization_alternatives,
            "gpu_backends": sorted(self.gpu_backends),
            "required_features": sorted(self.required_features),
            "notes": list(self.notes),
        }


# --- Per-entity requirement extraction ----------------------------------------


async def _apply_game(
    db: AsyncSession, game_id: uuid.UUID, req: CatalogRequirements
) -> None:
    game = (
        await db.execute(
            select(Game)
            .where(Game.id == game_id)
            .options(selectinload(Game.minimum_parts))
        )
    ).scalar_one_or_none()
    if game is None:
        return

    req.matched_names.append(game.title)
    req._raise_floor("min_storage_gb", game.min_storage_gb)
    for feature in game.hard_requirements or []:
        if feature and feature.strip():
            req.required_features.add(feature.strip())

    # Prefer the recommended tier over minimum: a user naming a game wants to
    # play it well, and "minimum" describes the spec at which it launches.
    by_tier = {p.tier: p for p in game.minimum_parts}
    part = by_tier.get(RequirementTier.RECOMMENDED.value) or by_tier.get(
        RequirementTier.MINIMUM.value
    )
    if part is not None:
        req._raise_floor("min_ram_gb", part.min_ram_gb)
        tier_label = part.tier
        detail = f"{game.title}: {tier_label} spec"
        if part.published_name:
            detail += f" calls for {part.published_name}"
        if part.min_ram_gb:
            detail += f", {part.min_ram_gb}GB RAM"
        req.notes.append(detail)
    else:
        req.notes.append(f"{game.title}: no published part requirements on file")


async def _apply_software(
    db: AsyncSession,
    software_id: uuid.UUID,
    req: CatalogRequirements,
    intensity: str | None,
) -> None:
    software = (
        await db.execute(
            select(Software)
            .where(Software.id == software_id)
            .options(selectinload(Software.tiers))
        )
    ).scalar_one_or_none()
    if software is None or not software.tiers:
        if software is not None:
            req.matched_names.append(software.name)
            req.notes.append(f"{software.name}: no tier requirements on file")
        return

    req.matched_names.append(software.name)
    # tiers are ordered by sort_order (relationship order_by), so the first is
    # the baseline workload and the last is the most demanding one on file.
    # "heavy" is the only intensity that justifies sizing for the top tier.
    tier = software.tiers[-1] if intensity == "heavy" else software.tiers[0]

    req._raise_floor("min_vram_gb", tier.min_vram_gb)
    req._raise_floor("min_ram_gb", tier.recommended_ram_gb or tier.min_ram_gb)
    req._raise_floor("min_storage_gb", tier.min_storage_gb)
    req._raise_floor("min_cores", tier.min_cores)

    detail = f"{software.name} ({tier.name}): GPU is {tier.gpu_importance}"
    if tier.min_vram_gb:
        detail += f", needs {tier.min_vram_gb}GB VRAM"
    if tier.recommended_ram_gb:
        detail += f", {tier.recommended_ram_gb}GB RAM recommended"
    if tier.prefers_single_thread:
        detail += "; latency-bound, favours single-thread speed"
    req.notes.append(detail)


async def _apply_ai_model(
    db: AsyncSession,
    model_id: uuid.UUID,
    req: CatalogRequirements,
    task: str | None,
) -> None:
    model = (
        await db.execute(
            select(AIModel)
            .where(AIModel.id == model_id)
            .options(selectinload(AIModel.workloads))
        )
    ).scalar_one_or_none()
    if model is None or not model.workloads:
        if model is not None:
            req.matched_names.append(model.name)
            req.notes.append(f"{model.name}: no workload requirements on file")
        return

    req.matched_names.append(model.name)

    preferred_tasks = _AI_TASK_PREFERENCE.get(task or "", ("inference",))
    workload = None
    for candidate_task in preferred_tasks:
        matches = [w for w in model.workloads if w.task == candidate_task]
        if matches:
            # Lowest sort_order within a task is the curated default precision.
            workload = sorted(matches, key=lambda w: w.sort_order)[0]
            break
    if workload is None:
        workload = sorted(model.workloads, key=lambda w: w.sort_order)[0]

    req._raise_floor(
        "min_vram_gb", workload.recommended_vram_gb or workload.min_vram_gb
    )
    req._raise_floor("min_ram_gb", workload.recommended_ram_gb or workload.min_ram_gb)
    req._raise_floor("min_storage_gb", workload.min_storage_gb)
    req.supports_multi_gpu = req.supports_multi_gpu or bool(workload.supports_multi_gpu)
    for backend in workload.gpu_backends or []:
        if backend and backend.strip():
            req.gpu_backends.add(backend.strip())
    for feature in workload.required_gpu_features or []:
        if feature and feature.strip():
            req.required_features.add(feature.strip())

    detail = (
        f"{model.name} ({workload.task} at {workload.precision}): "
        f"needs {workload.recommended_vram_gb or workload.min_vram_gb or '?'}GB VRAM"
    )
    if workload.supports_multi_gpu:
        detail += ", shards across GPUs"
    if workload.cpu_offload_capable:
        detail += ", can offload to system RAM when VRAM is short"
    req.notes.append(detail)

    # THE CHOSEN ROW IS ONE CELL OF A MATRIX, and quoting it alone reads as
    # "this model needs N GB" when the truth is "this model needs N GB at this
    # precision". ai_workloads is keyed on (model, task, precision) precisely
    # because the range is wide — so when the catalog holds other precisions for
    # the same task, show them. A step that can see 70B costs 140GB at fp16 and
    # 42GB at q4 can pick a card; a step shown only the first number buys for
    # the worst case, every time.
    alternates = [
        w
        for w in model.workloads
        if w.task == workload.task
        and w.id != workload.id
        and (w.recommended_vram_gb or w.min_vram_gb)
    ]
    if alternates:
        spread = ", ".join(
            f"{w.precision}: {w.recommended_vram_gb or w.min_vram_gb}GB"
            for w in sorted(
                alternates,
                key=lambda w: w.recommended_vram_gb or w.min_vram_gb or 0,
            )
        )
        req.notes.append(
            f"  {model.name} at other quantizations ({workload.task}): {spread}. "
            "The VRAM floor below assumes the default precision — a lower "
            "quantization fits a smaller card and is usually the better buy."
        )
        req.quantization_alternatives = True


_APPLIERS = {
    EmbeddedEntity.GAME.value: "game",
    EmbeddedEntity.SOFTWARE.value: "software",
    EmbeddedEntity.AI_MODEL.value: "ai_model",
}


class _Hit(NamedTuple):
    """A resolved catalog row, from either the alias path or vector search.

    Deliberately carries no distance: once a term has resolved, the two paths
    are equivalent to everything downstream, and an exact alias hit has no
    distance to report. Anything that needs to distinguish them should read the
    log line, not infer it from a sentinel value.
    """

    entity_type: str
    entity_id: uuid.UUID


# Which table each catalog entity lives in, and the column holding its display
# name. Ordered games-first because game titles are what users name most.
_ALIAS_TABLES: tuple[tuple[str, Any, Any], ...] = (
    (EmbeddedEntity.GAME.value, Game, Game.title),
    (EmbeddedEntity.SOFTWARE.value, Software, Software.name),
    (EmbeddedEntity.AI_MODEL.value, AIModel, AIModel.name),
)


def _normalize_term(value: str) -> str:
    """Fold a user's phrasing to a comparable form.

    Mirrors crud/components._normalize in spirit: aliases are typed by hand in
    the admin panel and by users in chat, and neither is going to agree on case
    or spacing. "Rainbow 6", "rainbow6" and "RAINBOW 6" must all be the same
    key, or curating aliases becomes an exercise in guessing punctuation.
    """
    return re.sub(r"[\s\-_:]+", "", value.strip().lower())


async def _match_by_alias(db: AsyncSession, term: str) -> _Hit | None:
    """Resolve a term against catalog titles and curated aliases, exactly.

    Normalization happens in Python rather than SQL: the comparison has to fold
    punctuation and spacing the same way on both sides, and doing that in the
    query would need an expression index this does not have (see migration
    f4a5b6c7d8e9 on why there is no index). These catalogs are small enough that
    scanning them is cheaper than the embedding API call this replaces.
    """
    target = _normalize_term(term)
    if not target:
        return None

    for entity_type, model, name_column in _ALIAS_TABLES:
        rows = await db.execute(select(model.id, name_column, model.aliases))
        for row in rows:
            names = [row[1], *(row[2] or [])]
            if any(name and _normalize_term(name) == target for name in names):
                logger.info(
                    "catalog match: %r resolved to %s %r by exact name/alias",
                    term,
                    entity_type,
                    row[1],
                )
                return _Hit(entity_type, row[0])
    return None


# --- Entry point --------------------------------------------------------------


async def resolve_requirements(
    db: AsyncSession,
    terms: list[str],
    *,
    ai_workload: str | None = None,
    workload_intensity: str | None = None,
) -> CatalogRequirements:
    """Match each free-text term to a catalog row and aggregate its requirements.

    `terms` are the raw strings from BuildProfile.games + .workloads. Each is
    searched independently rather than concatenated, because one vector built
    from "Cyberpunk 2077, DaVinci Resolve, Llama 70B" lands between all three
    and matches none of them well.
    """
    req = CatalogRequirements()
    cleaned = [t.strip() for t in (terms or []) if t and t.strip()][:_MAX_TERMS]
    if not cleaned:
        return req

    seen: set[tuple[str, uuid.UUID]] = set()
    for term in cleaned:
        # Exact name or alias first. A curated synonym is a fact, not a
        # similarity: resolving "R6" by approximate nearest neighbour when the
        # catalog literally records it as an alias of Rainbow Six Siege is both
        # slower (an embedding API call) and less reliable (a short token has to
        # out-compete every other row's prose to clear the distance cutoff).
        # This path costs one indexed-ish scan over a few thousand rows and
        # cannot return the wrong answer.
        exact = await _match_by_alias(db, term)
        if exact is not None:
            entity_type, entity_id = exact
        else:
            try:
                hits = await store.search_catalog(
                    db, term, limit=1, max_distance=_MATCH_MAX_DISTANCE
                )
            except Exception:
                # A vector-search failure must not take the build down with it.
                logger.exception("catalog search failed for %r", term)
                continue

            if not hits:
                req.unmatched_terms.append(term)
                continue
            entity_type, entity_id = hits[0].entity_type, hits[0].entity_id

        key = (entity_type, entity_id)
        if key in seen:
            continue
        seen.add(key)
        hit = _Hit(entity_type, entity_id)

        kind = _APPLIERS.get(hit.entity_type)
        if kind == "game":
            await _apply_game(db, hit.entity_id, req)
        elif kind == "software":
            await _apply_software(db, hit.entity_id, req, workload_intensity)
        elif kind == "ai_model":
            await _apply_ai_model(db, hit.entity_id, req, ai_workload)

    if req.matched_names:
        logger.info(
            "catalog match: %d/%d terms resolved (%s)",
            len(req.matched_names),
            len(cleaned),
            ", ".join(req.matched_names),
        )
    return req
