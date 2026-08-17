"""
recording.py
============
Best-effort telemetry for the DSPy recommender pipeline.

A `BuildRecorder` accumulates one `build_sessions` row plus one
`module_decisions` row per `Decide*` call, then hands the lot to a write-behind
buffer on Valkey. `drain_pending()` — called from turn_runner after save_turn,
and by the telemetry-drain job — is what actually writes them to Postgres.

WHY THE BUFFER IS IN THE MIDDLE. The recorder is driven from inside a LangGraph
node, and that path may read from Postgres but never write to it; persistence is
save_turn's job, outside the graph. Recording used to commit directly from the
`build` node, which both broke that rule and made a re-executed node emit a
second build_sessions row rather than overwriting the first. See
app/services/telemetry_buffer.py.

Writes are still fire-and-forget: any failure is logged and swallowed so
recording can never block or fail a user's build.

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
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import dspy
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.logging import set_build_session_id
from app.core.tracing import attach_run_metadata
from app.models.build_session import BuildSession, BuildSessionStatus, ModuleDecision
from app.models.pcparts import PCPart
from app.services import telemetry_buffer

logger = logging.getLogger(__name__)

# Stands in for model_name on decisions no model made. Filter it out of any GEPA
# extraction query — see BuildRecorder.record_deterministic_decision.
DETERMINISTIC_MODEL_NAME = "deterministic/dominance-gate"


def _utc_now_iso() -> str:
    """Timezone-aware UTC, as ISO-8601. A string because it has to survive a
    JSON round-trip through Valkey; parsed back by _as_datetime at drain time."""
    return datetime.now(UTC).isoformat()


# --- In-memory decision record (flushed to module_decisions at finish) --------


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
    # WHEN THE DECISION ACTUALLY HAPPENED, not when it reached Postgres. The two
    # used to be the same thing; now telemetry is buffered on Valkey and drained
    # afterwards, so the column's server_default would stamp drain time — which
    # for anything a dead worker left behind is whenever the backstop job next
    # ran. That would be wrong twice over: it reorders a build's decisions
    # against other builds, and module_decisions.created_at is part of
    # ix_module_decisions_category_pipeline_created, the index every GEPA
    # extraction windows on. A sample landing in the wrong cohort is a silent
    # data-quality bug, so the timestamp is captured here and written explicitly.
    recorded_at: str = field(default_factory=lambda: _utc_now_iso())


# --- Usage / prompt extraction helpers ----------------------------------------


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


# --- BuildRecorder ------------------------------------------------------------


class BuildRecorder:
    """Accumulates telemetry for one pipeline run and flushes it best-effort."""

    def __init__(
        self,
        request: Any,
        pipeline_version: str,
        user_id: uuid.UUID | str | None = None,
        conversation_id: uuid.UUID | str | None = None,
    ) -> None:
        self.session_id = uuid.uuid4()
        # When the build STARTED, captured now rather than left to the column's
        # server_default — which, with telemetry buffered on Valkey and drained
        # afterwards, would record when the row was written instead of when the
        # build ran. See _DecisionRecord.recorded_at for why that distinction
        # has teeth.
        self.started_at = _utc_now_iso()
        # Tag every log line emitted for the rest of this run, so a build can be
        # followed across replicas without threading the id through call sites.
        set_build_session_id(str(self.session_id))
        # Same id onto the trace, which is the join between the two telemetry
        # systems: LangSmith holds the conversation and the prompts, Postgres
        # holds the candidate sets and the chosen parts, and this is the only
        # key that appears in both. Without it a low-scoring trace cannot be
        # resolved to the candidate list that produced it.
        attach_run_metadata(build_session_id=self.session_id)
        # Coerced rather than stored raw: build_sessions.conversation_id is a
        # UUID column, and a guest turn's thread id is the synthetic string
        # "turn:<uuid>" (see chat_pipeline.run_chat_turn). Passing that straight
        # through would fail the whole telemetry flush on a DataError — and
        # telemetry is never allowed to break a build. Anything unparseable
        # becomes NULL, which is already the guest case's correct answer.
        self.conversation_id: uuid.UUID | None = None
        if conversation_id is not None:
            try:
                self.conversation_id = uuid.UUID(str(conversation_id))
            except (ValueError, AttributeError, TypeError):
                logger.debug(
                    "conversation_id %r is not a UUID; recording it as NULL",
                    conversation_id,
                )
        self.pipeline_version = pipeline_version
        self.user_id = user_id
        try:
            self.input_profile = request.model_dump()
        except Exception:  # pragma: no cover - defensive
            self.input_profile = {}
        budget_usd = getattr(request, "budget_usd", None)
        self.budget_cents = int(budget_usd * 100) if budget_usd is not None else None
        self._decisions: list[_DecisionRecord] = []
        # Hardware floors resolved from the catalogs for what the user named.
        # Held on the recorder rather than passed per decision because they are
        # a property of the build, not of any one step — every decision in a run
        # was made under the same requirements, and copying them onto each row
        # is what lets a single decision be scored without a join.
        self.catalog_requirements: dict | None = None
        # Reference build resolved in parallel; recorded on the same session row.
        self.reference_build_key: str | None = None
        self.reference_build: dict | None = None

    # -- surviving a pause -------------------------------------------------

    def to_dict(self) -> dict:
        """A JSON-safe snapshot, so a run paused at the case step can be
        finished later as ONE build_sessions row rather than two.

        Carrying the recorder across the pause is not merely tidier than
        flushing twice — it is the only thing that works. The drain upserts
        with `on_conflict_do_nothing` on the session id (see _drain), so a row
        written at pause time would win permanently and the completed build's
        decisions would be silently dropped.
        """
        return {
            "session_id": str(self.session_id),
            "started_at": self.started_at,
            "conversation_id": (
                str(self.conversation_id) if self.conversation_id else None
            ),
            "pipeline_version": self.pipeline_version,
            "user_id": str(self.user_id) if self.user_id else None,
            "input_profile": self.input_profile,
            "budget_cents": self.budget_cents,
            "decisions": [asdict(d) for d in self._decisions],
            "catalog_requirements": self.catalog_requirements,
            "reference_build_key": self.reference_build_key,
            "reference_build": self.reference_build,
        }

    @classmethod
    def restore(cls, data: dict) -> BuildRecorder:
        """Rebuild a recorder from to_dict().

        Bypasses __init__ rather than feeding it stand-in arguments, because
        __init__'s job is to *start* a run — it mints a session id and stamps
        started_at, both of which would overwrite the values being restored.
        Its two side effects are re-applied deliberately below: the resume runs
        in a different process than the pause, and its log lines and spans
        should carry the same build session id as the half that came before.
        """
        recorder = cls.__new__(cls)
        recorder.session_id = uuid.UUID(data["session_id"])
        recorder.started_at = data["started_at"]
        conversation_id = data.get("conversation_id")
        recorder.conversation_id = (
            uuid.UUID(conversation_id) if conversation_id else None
        )
        recorder.pipeline_version = data["pipeline_version"]
        recorder.user_id = data.get("user_id")
        recorder.input_profile = data.get("input_profile") or {}
        recorder.budget_cents = data.get("budget_cents")
        recorder._decisions = [
            _DecisionRecord(**d) for d in data.get("decisions") or []
        ]
        recorder.catalog_requirements = data.get("catalog_requirements")
        recorder.reference_build_key = data.get("reference_build_key")
        recorder.reference_build = data.get("reference_build")

        set_build_session_id(str(recorder.session_id))
        attach_run_metadata(build_session_id=recorder.session_id)
        return recorder

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

    def set_catalog_requirements(self, requirements: Any | None) -> None:
        """Snapshot the resolved catalog floors onto this run.

        Call once, before the first Decide* step, so every recorded decision
        carries the requirements it was actually made under. Accepts either a
        CatalogRequirements or a plain dict; None (nothing named, or nothing
        matched) leaves the column NULL, which the appropriateness metrics read
        as "sufficiency was not measurable" rather than as "requirements met".
        """
        try:
            if requirements is None:
                self.catalog_requirements = None
            elif isinstance(requirements, dict):
                self.catalog_requirements = requirements
            else:
                self.catalog_requirements = requirements.to_dict()
        except Exception:  # pragma: no cover - defensive
            logger.debug("set_catalog_requirements failed", exc_info=True)

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
        """Schedule the best-effort buffer write. Returns immediately.

        Buffers to Valkey rather than writing to Postgres, because this is
        called from inside a LangGraph node and that path is not allowed to
        write to the database — see app/services/telemetry_buffer.py. The
        Postgres insert happens in drain_pending(), off the graph path.
        """
        try:
            asyncio.create_task(self._buffer(status))
        except RuntimeError:
            # No running loop (e.g. sync test context) — buffer synchronously.
            asyncio.run(self._buffer(status))

    # -- internal ----------------------------------------------------------

    async def _buffer(self, status: BuildSessionStatus) -> None:
        """Serialize this run and hand it to the write-behind buffer.

        Note what is NOT done here: chosen_name is not resolved to a part id.
        That resolution is a SELECT against pc_parts, and doing it at drain time
        keeps this path free of database access altogether rather than merely
        free of writes — which is a much easier property to keep true.
        """
        try:
            await telemetry_buffer.push(self._payload(status))
        except Exception:  # pragma: no cover - defensive
            logger.exception(
                "build telemetry buffering failed (session_id=%s)", self.session_id
            )

    def _payload(self, status: BuildSessionStatus) -> dict:
        """JSON-safe snapshot of the whole run, shaped for drain_pending."""
        agg = self._aggregate()
        return {
            "session_id": str(self.session_id),
            "user_id": str(self.user_id) if self.user_id else None,
            "conversation_id": (
                str(self.conversation_id) if self.conversation_id else None
            ),
            "pipeline_version": self.pipeline_version,
            "budget_cents": self.budget_cents,
            "input_profile": self.input_profile,
            "reference_build_key": self.reference_build_key,
            "reference_build": self.reference_build,
            "catalog_requirements": self.catalog_requirements,
            "status": status.value,
            # The build's own chronology, carried through the buffer so the
            # drain can write it instead of letting now() stamp drain time.
            "started_at": self.started_at,
            "finished_at": _utc_now_iso(),
            # Decimal is not JSON-serializable; the buffer's default=str would
            # stringify it, and the drain would then hand Postgres a string for
            # a numeric column. Converted here where the type is still known.
            "total_cost_usd": (
                str(agg["total_cost_usd"])
                if agg["total_cost_usd"] is not None
                else None
            ),
            "total_latency_ms": agg["total_latency_ms"],
            "compatibility_override_count": agg["compatibility_override_count"],
            "budget_delta_cents": agg["budget_delta_cents"],
            "decisions": [asdict(d) for d in self._decisions],
        }

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


# --- Drain — the only place build telemetry reaches Postgres ------------------
# Called from turn_runner after save_turn, and by the telemetry-drain job as a
# backstop for anything a dead worker left buffered. Never called from a graph
# node: that is the whole point of the buffer sitting in front of it.

# How many buffered runs one drain pass persists. Bounded so a large backlog is
# worked off across several turns rather than stalling one of them.
DRAIN_BATCH = 20


async def drain_pending(limit: int = DRAIN_BATCH) -> int:
    """Persist buffered build telemetry. Returns how many sessions were written.

    IDEMPOTENT BY SESSION ID. Every insert is ON CONFLICT DO NOTHING against
    build_sessions.id, and a session already present skips its decisions
    entirely. That is what makes the peek/commit/trim cycle safe without
    consumer groups: a payload delivered twice — because a trim failed, or two
    workers drained concurrently — becomes a no-op rather than a duplicate row
    or an integrity error.

    Never raises. A drain failure must not affect the turn that triggered it;
    the entries stay buffered and the next pass retries them.
    """
    from app.core.db import AsyncSessionLocal

    try:
        payloads = await telemetry_buffer.peek(limit)
    except Exception:  # pragma: no cover - defensive
        logger.warning("telemetry drain could not read the buffer", exc_info=True)
        return 0
    if not payloads:
        return 0

    written = 0
    try:
        async with AsyncSessionLocal() as db:
            for payload in payloads:
                if await _persist_session(db, payload):
                    written += 1
            await db.commit()
    except Exception:
        # Left in the buffer deliberately: unlike a chat turn there is no user
        # waiting, so retrying next pass costs nothing and losing a GEPA sample
        # is not recoverable.
        logger.warning("telemetry drain failed; entries stay buffered", exc_info=True)
        return 0

    # Trim by how many were READ, not how many were written — a payload that
    # failed to parse or was already present must still leave the queue, or it
    # blocks everything behind it forever.
    await telemetry_buffer.ack(len(payloads))
    if written:
        logger.info("persisted telemetry for %d build session(s)", written)
    return written


def _as_uuid(value: Any) -> uuid.UUID | None:
    """JSON round-trips UUIDs as strings; UUID columns want the object back."""
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _as_decimal(value: Any) -> Decimal | None:
    """Same story for Numeric columns — _payload stringifies Decimal to survive
    JSON, and handing that string to the driver is not the same as handing it a
    number."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError, TypeError):
        return None


def _as_datetime(value: Any) -> datetime | None:
    """Parse a buffered ISO timestamp back into an aware datetime.

    Returns None on anything unparseable, which lets the column's server_default
    take over — a slightly wrong timestamp beats a failed insert, and the loss is
    visible because it will be the only row stamped at drain time.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        logger.warning("telemetry payload has unparseable timestamp %r", value)
        return None
    # A naive datetime against a timestamptz column is read as server-local
    # time, which on a UTC pod is right by accident and wrong anywhere else.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _as_status(value: Any) -> BuildSessionStatus:
    """The status column is a native pg enum; reconstruct it from its value.

    Falls back to ERROR rather than raising: a run whose status did not survive
    the buffer is a run that went wrong somewhere, and recording it as completed
    would put a lie in the training data.
    """
    try:
        return BuildSessionStatus(value)
    except ValueError:
        logger.warning(
            "telemetry payload has unknown status %r; recording error", value
        )
        return BuildSessionStatus.ERROR


async def _persist_session(db, payload: dict) -> bool:
    """Insert one buffered run. False if it was already there or unusable."""
    session_id = _as_uuid(payload.get("session_id"))
    if session_id is None:
        logger.warning("telemetry payload has no usable session_id; dropping it")
        return False

    decisions = payload.get("decisions") or []

    # Resolve chosen part ids here rather than at record time, so the graph path
    # touches the database not at all — see BuildRecorder._buffer.
    final_build: dict[str, str] = {}
    resolved: list[str | None] = []
    for d in decisions:
        part_id = await _resolve_part_id(db, d.get("chosen_name"))
        resolved.append(str(part_id) if part_id else None)
        if part_id:
            final_build[d.get("category") or ""] = str(part_id)

    # Only set when they parsed: passing created_at=None to a NOT NULL column
    # overrides the server_default with an explicit NULL and fails the insert,
    # where omitting the key lets now() fill it as it always did.
    session_times = {
        key: value
        for key, value in (
            ("created_at", _as_datetime(payload.get("started_at"))),
            ("updated_at", _as_datetime(payload.get("finished_at"))),
        )
        if value is not None
    }

    inserted = await db.execute(
        pg_insert(BuildSession)
        .values(
            **session_times,
            id=session_id,
            user_id=_as_uuid(payload.get("user_id")),
            conversation_id=_as_uuid(payload.get("conversation_id")),
            pipeline_version=payload.get("pipeline_version"),
            budget_cents=payload.get("budget_cents"),
            input_profile=payload.get("input_profile"),
            final_build=final_build or None,
            reference_build_key=payload.get("reference_build_key"),
            reference_build=payload.get("reference_build"),
            status=_as_status(payload.get("status")),
            total_cost_usd=_as_decimal(payload.get("total_cost_usd")),
            total_latency_ms=payload.get("total_latency_ms"),
            compatibility_override_count=payload.get("compatibility_override_count"),
            budget_delta_cents=payload.get("budget_delta_cents"),
        )
        .on_conflict_do_nothing(index_elements=["id"])
        .returning(BuildSession.id)
    )
    if inserted.scalar_one_or_none() is None:
        # Already persisted by an earlier pass. Its decisions are already there
        # too, so writing them again would duplicate every row.
        return False

    catalog_requirements = payload.get("catalog_requirements")
    for d, part_id in zip(decisions, resolved, strict=True):
        output = dict(d.get("output_decision") or {})
        if part_id:
            output["part_id"] = part_id
        recorded_at = _as_datetime(d.get("recorded_at"))
        db.add(
            ModuleDecision(
                # When the step ran, not when it was drained. This column is in
                # ix_module_decisions_category_pipeline_created, so GEPA cohort
                # windows are computed against it.
                **({"created_at": recorded_at} if recorded_at else {}),
                session_id=session_id,
                pipeline_version=payload.get("pipeline_version"),
                category=d.get("category"),
                sequence_order=d.get("sequence_order"),
                signature_name=d.get("signature_name"),
                signature_version=d.get("signature_version"),
                candidate_set=d.get("candidate_set"),
                input_state=d.get("input_state"),
                # Denormalized onto every row on purpose: it makes a decision
                # scoreable on its own, without joining back to the session,
                # which is what the metric and the trainset builder both want.
                catalog_requirements=catalog_requirements,
                raw_prompt_hash=d.get("raw_prompt_hash"),
                output_decision=output or None,
                tokens_in=d.get("tokens_in"),
                tokens_out=d.get("tokens_out"),
                cost_usd=d.get("cost_usd"),
                latency_ms=d.get("latency_ms"),
                was_override=d.get("was_override"),
                model_name=d.get("model_name"),
            )
        )
    return True


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
