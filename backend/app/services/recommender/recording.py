"""
recording.py
============
Best-effort telemetry for the DSPy recommender pipeline.

A `BuildRecorder` accumulates one `build_sessions` row plus one
`module_decisions` row per `Decide*` call, then flushes them all in a single
detached task on a fresh DB session. Writes are fire-and-forget: any failure is
logged and swallowed so recording can never block or fail a user's build.

The per-decision snapshot is captured *verbatim* (candidate set, input state,
prompt hash) so a GEPA replay months later isn't corrupted by drift in
pc_parts pricing/inventory.

If a pipeline run has no recorder, none of this runs — zero behavior change.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import dspy
from sqlalchemy import func, select

from app.core.logging import set_build_session_id
from app.models.build_session import BuildSession, BuildSessionStatus, ModuleDecision
from app.models.pcparts import PCPart

logger = logging.getLogger(__name__)

# Stands in for model_name on decisions no model made. Filter it out of any GEPA
# extraction query — see BuildRecorder.record_deterministic_decision.
DETERMINISTIC_MODEL_NAME = "deterministic/dominance-gate"


# ---------------------------------------------------------------------------
# In-memory decision record (flushed to module_decisions at finish)
# ---------------------------------------------------------------------------


@dataclass
class _DecisionRecord:
    category: str
    sequence_order: int
    signature_name: str
    signature_version: int
    candidate_set: list[dict] | None
    input_state: dict | None
    raw_prompt_hash: str | None
    output_decision: dict | None
    tokens_in: int | None
    tokens_out: int | None
    cost_usd: float | None
    latency_ms: int | None
    was_override: bool
    model_name: str | None
    chosen_name: str | None
    chosen_price_usd: float | None


# ---------------------------------------------------------------------------
# Usage / prompt extraction helpers
# ---------------------------------------------------------------------------


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _usage_field(usage: Any, key: str) -> int | None:
    """litellm usage may be a dict or a pydantic-ish object."""
    if usage is None:
        return None
    if isinstance(usage, dict):
        return _as_int(usage.get(key))
    return _as_int(getattr(usage, key, None))


def extract_usage(
    prediction: dspy.Prediction,
    history_entry: dict | None,
) -> tuple[int | None, int | None, float | None, str | None, str | None]:
    """
    Pull (tokens_in, tokens_out, cost_usd, model_name, raw_prompt_hash) from a
    DSPy prediction plus the LM history entry for this call.

    Tokens come preferentially from prediction.get_lm_usage() (per-prediction,
    isolated); cost + rendered prompt come from the history entry, which under
    OpenRouter carries litellm's computed cost and the actual messages.
    """
    tokens_in = tokens_out = None
    cost = None
    model = None

    try:
        usage = prediction.get_lm_usage()  # {model_name: {prompt_tokens, ...}}
        for m, u in (usage or {}).items():
            model = model or m
            ti = _usage_field(u, "prompt_tokens")
            to = _usage_field(u, "completion_tokens")
            tokens_in = (tokens_in or 0) + ti if ti is not None else tokens_in
            tokens_out = (tokens_out or 0) + to if to is not None else tokens_out
    except Exception:  # pragma: no cover - defensive
        logger.debug("get_lm_usage failed", exc_info=True)

    messages = None
    if history_entry:
        cost = history_entry.get("cost")
        model = model or history_entry.get("model")
        messages = history_entry.get("messages") or history_entry.get("prompt")
        if tokens_in is None and tokens_out is None:
            usage = history_entry.get("usage")
            tokens_in = _usage_field(usage, "prompt_tokens")
            tokens_out = _usage_field(usage, "completion_tokens")

    return tokens_in, tokens_out, cost, model, _prompt_hash(messages)


def _prompt_hash(messages: Any) -> str | None:
    if not messages:
        return None
    try:
        blob = json.dumps(messages, sort_keys=True, default=str)
    except Exception:  # pragma: no cover - defensive
        blob = str(messages)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _output_decision(prediction: dspy.Prediction) -> dict:
    try:
        return dict(prediction.toDict())
    except Exception:  # pragma: no cover - defensive
        return {}


def _parse_candidates(candidates_json: str | None) -> list[dict] | None:
    if not candidates_json:
        return None
    try:
        parsed = json.loads(candidates_json)
        return parsed if isinstance(parsed, list) else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# BuildRecorder
# ---------------------------------------------------------------------------


class BuildRecorder:
    """Accumulates telemetry for one pipeline run and flushes it best-effort."""

    def __init__(
        self,
        request: Any,
        pipeline_version: str,
        user_id: uuid.UUID | str | None = None,
    ) -> None:
        self.session_id = uuid.uuid4()
        # Tag every log line emitted for the rest of this run, so a build can be
        # followed across replicas without threading the id through call sites.
        set_build_session_id(str(self.session_id))
        self.pipeline_version = pipeline_version
        self.user_id = user_id
        try:
            self.input_profile = request.model_dump()
        except Exception:  # pragma: no cover - defensive
            self.input_profile = {}
        budget_usd = getattr(request, "budget_usd", None)
        self.budget_cents = int(budget_usd * 100) if budget_usd is not None else None
        self._decisions: list[_DecisionRecord] = []
        # Reference build resolved in parallel; recorded on the same session row.
        self.reference_build_key: str | None = None
        self.reference_build: dict | None = None

    def record_decision(
        self,
        *,
        category: str,
        sequence_order: int,
        signature_name: str,
        signature_version: int,
        candidates_json: str | None,
        input_state: dict | None,
        prediction: dspy.Prediction,
        history_entry: dict | None,
        chosen_name: str | None,
        latency_ms: int | None,
    ) -> None:
        """Capture one Decide* call. Never raises — telemetry must not break the run."""
        try:
            candidate_set = _parse_candidates(candidates_json)
            tokens_in, tokens_out, cost, model, prompt_hash = extract_usage(
                prediction, history_entry
            )

            candidate_names = {
                c.get("name") for c in (candidate_set or []) if isinstance(c, dict)
            }
            was_override = bool(chosen_name) and chosen_name not in candidate_names

            chosen_price = None
            for c in candidate_set or []:
                if isinstance(c, dict) and c.get("name") == chosen_name:
                    chosen_price = c.get("street_price_usd")
                    break

            self._decisions.append(
                _DecisionRecord(
                    category=category,
                    sequence_order=sequence_order,
                    signature_name=signature_name,
                    signature_version=signature_version,
                    candidate_set=candidate_set,
                    input_state=input_state,
                    raw_prompt_hash=prompt_hash,
                    output_decision=_output_decision(prediction),
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=cost,
                    latency_ms=latency_ms,
                    was_override=was_override,
                    model_name=model,
                    chosen_name=chosen_name,
                    chosen_price_usd=chosen_price,
                )
            )
        except Exception:  # pragma: no cover - defensive
            logger.debug("record_decision failed", exc_info=True)

    def record_deterministic_decision(
        self,
        *,
        category: str,
        sequence_order: int,
        signature_name: str,
        signature_version: int,
        candidates_json: str | None,
        input_state: dict | None,
        output_decision: dict,
        chosen_name: str | None,
        latency_ms: int | None,
    ) -> None:
        """Capture a step that was resolved without an LLM call.

        Written by the dominance gate in app/services/recommender/scoring.py,
        which short-circuits a step when one candidate is both cheaper and
        faster than every alternative. The row is recorded so the session's
        decision trail stays complete — a missing `cpu` or `gpu` row would read
        as a pipeline failure rather than as a step that had nothing to decide.

        model_name is the sentinel DETERMINISTIC_MODEL_NAME rather than NULL, so
        GEPA extraction can exclude these rows explicitly. They must be excluded:
        there is no prompt and no model output here, so as training examples
        they would teach the optimizer to imitate a rule it does not have.
        Token/cost fields stay NULL because nothing was spent — that is what
        makes the saving visible in the per-session cost aggregate.
        """
        try:
            self._decisions.append(
                _DecisionRecord(
                    category=category,
                    sequence_order=sequence_order,
                    signature_name=signature_name,
                    signature_version=signature_version,
                    candidate_set=_parse_candidates(candidates_json),
                    input_state=input_state,
                    raw_prompt_hash=None,
                    output_decision=output_decision,
                    tokens_in=None,
                    tokens_out=None,
                    cost_usd=None,
                    latency_ms=latency_ms,
                    was_override=False,
                    model_name=DETERMINISTIC_MODEL_NAME,
                    chosen_name=chosen_name,
                    chosen_price_usd=(output_decision or {}).get("street_price_usd"),
                )
            )
        except Exception:  # pragma: no cover - defensive
            logger.debug("record_deterministic_decision failed", exc_info=True)

    def set_reference_build(self, build_key: str | None, build: Any | None) -> None:
        """
        Attach the parallel-resolved reference build to this session.

        Call before finish() so it's flushed onto the same build_sessions row.
        Stored even when the DSPy build wins and is shown to the customer, so the
        run carries both builds for comparison.
        """
        try:
            self.reference_build_key = build_key
            if build is None:
                self.reference_build = None
            elif isinstance(build, dict):
                self.reference_build = build
            else:  # pydantic model or similar
                self.reference_build = getattr(
                    build, "model_dump", lambda: dict(build)
                )()
        except Exception:  # pragma: no cover - defensive
            logger.debug("set_reference_build failed", exc_info=True)

    def record_case_choice(self, case_name: str) -> None:
        """
        Overwrite the recorded case decision's chosen_name with the final pick.

        DecideCase is recorded with option_1 as chosen_name at decision time,
        but the actual case is selected afterwards (by the user, or auto-picked).
        Call this before finish() so final_build reflects the real choice.
        """
        try:
            for d in self._decisions:
                if d.category != "case":
                    continue
                d.chosen_name = case_name
                d.chosen_price_usd = None
                candidate_names = set()
                for c in d.candidate_set or []:
                    if not isinstance(c, dict):
                        continue
                    candidate_names.add(c.get("name"))
                    if c.get("name") == case_name:
                        d.chosen_price_usd = c.get("street_price_usd")
                d.was_override = case_name not in candidate_names
                d.output_decision = dict(d.output_decision or {})
                d.output_decision["case_selected"] = case_name
                break
        except Exception:  # pragma: no cover - defensive
            logger.debug("record_case_choice failed", exc_info=True)

    def finish(self, status: BuildSessionStatus = BuildSessionStatus.COMPLETED) -> None:
        """Schedule the best-effort flush. Returns immediately; never awaited."""
        try:
            asyncio.create_task(self._flush(status))
        except RuntimeError:
            # No running loop (e.g. sync test context) — flush synchronously.
            asyncio.run(self._flush(status))

    # -- internal ----------------------------------------------------------

    def _aggregate(self) -> dict:
        total_cost = sum(
            (
                Decimal(str(d.cost_usd))
                for d in self._decisions
                if d.cost_usd is not None
            ),
            Decimal("0"),
        )
        total_latency = sum(d.latency_ms or 0 for d in self._decisions)
        override_count = sum(1 for d in self._decisions if d.was_override)
        final_price_usd = sum(
            d.chosen_price_usd
            for d in self._decisions
            if d.chosen_price_usd is not None
        )
        budget_delta = None
        if self.budget_cents is not None:
            budget_delta = round(final_price_usd * 100) - self.budget_cents
        return {
            "total_cost_usd": total_cost if self._decisions else None,
            "total_latency_ms": total_latency or None,
            "compatibility_override_count": override_count,
            "budget_delta_cents": budget_delta,
        }

    async def _flush(self, status: BuildSessionStatus) -> None:
        # Imported lazily to avoid a circular import at module load.
        from app.core.db import AsyncSessionLocal

        try:
            agg = self._aggregate()
            async with AsyncSessionLocal() as db:
                # Resolve chosen part ids up front (also builds final_build map).
                final_build: dict[str, str] = {}
                resolved: dict[int, str | None] = {}
                for idx, d in enumerate(self._decisions):
                    part_id = await _resolve_part_id(db, d.chosen_name)
                    resolved[idx] = str(part_id) if part_id else None
                    if part_id:
                        final_build[d.category] = str(part_id)

                db.add(
                    BuildSession(
                        id=self.session_id,
                        user_id=self.user_id,
                        pipeline_version=self.pipeline_version,
                        budget_cents=self.budget_cents,
                        input_profile=self.input_profile,
                        final_build=final_build or None,
                        reference_build_key=self.reference_build_key,
                        reference_build=self.reference_build,
                        status=status,
                        **agg,
                    )
                )
                for idx, d in enumerate(self._decisions):
                    output = dict(d.output_decision or {})
                    if resolved[idx]:
                        output["part_id"] = resolved[idx]
                    db.add(
                        ModuleDecision(
                            session_id=self.session_id,
                            pipeline_version=self.pipeline_version,
                            category=d.category,
                            sequence_order=d.sequence_order,
                            signature_name=d.signature_name,
                            signature_version=d.signature_version,
                            candidate_set=d.candidate_set,
                            input_state=d.input_state,
                            raw_prompt_hash=d.raw_prompt_hash,
                            output_decision=output or None,
                            tokens_in=d.tokens_in,
                            tokens_out=d.tokens_out,
                            cost_usd=d.cost_usd,
                            latency_ms=d.latency_ms,
                            was_override=d.was_override,
                            model_name=d.model_name,
                        )
                    )
                await db.commit()
        except Exception:  # pragma: no cover - best-effort
            logger.exception(
                "build telemetry flush failed (session_id=%s)", self.session_id
            )


async def _resolve_part_id(db, name: str | None) -> uuid.UUID | None:
    """Resolve a chosen part name to its pc_parts id (case-insensitive)."""
    if not name:
        return None
    try:
        result = await db.execute(
            select(PCPart.id).where(func.lower(PCPart.name) == name.lower()).limit(1)
        )
        return result.scalar_one_or_none()
    except Exception:  # pragma: no cover - defensive
        logger.debug("part id resolution failed for %r", name, exc_info=True)
        return None
