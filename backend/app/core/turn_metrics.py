"""Domain metrics for chat turns, separate from HTTP instrumentation.

app/core/metrics.py instruments the *framework* — request rates, latencies,
in-flight counts — which is the same set any FastAPI service would want. These
are about what a turn does, and most of them only ever move on the worker.

WHY THESE FOUR. Each one exists to answer a question that the HTTP metrics
cannot, because the work no longer happens inside a request:

  turn_commits_total{result}   Did the turn reach Postgres? A failed commit is
                               invisible from the API side — the user saw their
                               build, the stream terminated normally, and only
                               this counter and a retained buffer record that
                               nothing was written.
  chat_buffers_retained        How many turns are sitting unpersisted right now.
                               The counter above catches the event; this catches
                               the backlog, including events missed while an
                               alert was silenced.
  turn_duration_seconds        How long turns take once dequeued. builder's
                               request histogram measures time-to-first-byte
                               (should_exclude_streaming_duration=True), so it
                               says nothing about total build time.
  turns_inflight               Worker saturation. The CPU-based signal
                               understates it for the same reason it does on
                               builder: turns block on OpenRouter, not on CPU.

Registered on the default registry, so the exporter in app/core/metrics.py
serves them with no extra wiring and GMP scrapes them through the same
PodMonitoring.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# `result` is a bounded label with exactly three values — never the exception
# text, which would mint a series per distinct error message.
TURN_COMMITS = Counter(
    "palladium_turn_commits_total",
    "Chat turns by persistence outcome.",
    ["result"],  # committed | failed | skipped
)

CHAT_BUFFERS_RETAINED = Gauge(
    "palladium_chat_buffers_retained",
    "Chat buffers still in Valkey, i.e. turns that have not reached Postgres. "
    "Steady state is 0.",
)

TURN_DURATION = Histogram(
    "palladium_turn_duration_seconds",
    "Wall time to run one chat turn, from dequeue to terminal stream entry.",
    # Tuned to _DSPY_CHAT_TIMEOUT_S (180s): the interesting region is 10-180s,
    # and the 240 bucket exists so timeouts are distinguishable from merely slow
    # turns rather than both landing in +Inf.
    buckets=(1, 5, 10, 20, 30, 45, 60, 90, 120, 180, 240),
)

TURNS_INFLIGHT = Gauge(
    "palladium_turns_inflight",
    "Chat turns currently executing in this process.",
)
