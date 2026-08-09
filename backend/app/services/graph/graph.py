"""Assembly and caching of the compiled chat turn graph.

    START → collect → route ─┬─ (incomplete) → ask   ──────→ finalize → END
                             └─ (complete)   → build → present → finalize → END

The builder is one node, not ten. Its ten DSPy steps have their own sequencing,
their own budget allocation and their own telemetry recorder in
app/services/recommender/dspy_pipeline.py; splitting them across graph nodes
would duplicate all three and buy nothing, because nothing branches between them.
"""

from __future__ import annotations

import logging

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.core.valkey import get_client
from app.services.graph import nodes
from app.services.graph.checkpoint import AsyncValkeySaver
from app.services.graph.state import ChatTurnState

logger = logging.getLogger(__name__)

# Two compiled graphs, at most, for the life of the process: one checkpointed on
# Valkey and one on memory. Compilation is pure structure — no connections, no
# per-conversation state — so caching is safe, and the checkpointer itself is
# the only thing that differs between them.
_graph: CompiledStateGraph | None = None
_fallback_graph: CompiledStateGraph | None = None


def build_graph() -> StateGraph:
    """The graph's shape, uncompiled and with no checkpointer bound.

    Split out from _build so LangGraph Studio can reach the topology without
    reaching the Valkey checkpointer with it — the dev server brings its own
    persistence and rejects a graph that arrives with one already attached.
    """
    builder: StateGraph = StateGraph(ChatTurnState)

    builder.add_node("collect", nodes.collect)
    builder.add_node("route", nodes.route)
    builder.add_node("ask", nodes.ask)
    builder.add_node("build", nodes.build)
    builder.add_node("present", nodes.present)
    builder.add_node("finalize", nodes.finalize)

    builder.add_edge(START, "collect")
    builder.add_edge("collect", "route")
    builder.add_conditional_edges(
        "route", nodes.should_build, {"ask": "ask", "build": "build"}
    )
    builder.add_edge("ask", "finalize")
    builder.add_edge("build", "present")
    builder.add_edge("present", "finalize")
    builder.add_edge("finalize", END)

    return builder


def _build(checkpointer: BaseCheckpointSaver) -> CompiledStateGraph:
    return build_graph().compile(checkpointer=checkpointer)


async def get_graph() -> CompiledStateGraph:
    """The compiled graph, checkpointed on Valkey when Valkey is reachable.

    Falls back to an in-memory checkpointer rather than failing, matching the
    contract the rest of app/core/valkey.py keeps: losing resumability is not a
    reason to refuse to answer the user. The fallback is per-process and its
    state dies with the pod, which is exactly the pre-graph behaviour.

    Reachability is checked per call, not cached, because Valkey may come up
    after the process did — but get_client() latches its own failure, so the
    check is a dictionary lookup after the first attempt rather than a probe.
    """
    global _graph, _fallback_graph

    if await get_client() is not None:
        if _graph is None:
            _graph = _build(AsyncValkeySaver())
        return _graph

    if _fallback_graph is None:
        from langgraph.checkpoint.memory import InMemorySaver

        logger.info(
            "Valkey unavailable; chat graph is using an in-memory checkpointer. "
            "Conversation state will not survive this process."
        )
        _fallback_graph = _build(InMemorySaver())
    return _fallback_graph


def reset_for_tests() -> None:
    """Drop both cached graphs, so a test that swaps the Valkey client sees it."""
    global _graph, _fallback_graph
    _graph = None
    _fallback_graph = None
