"""Pub/Sub subscriber that runs chat turns off the request path.

Runs as its own Deployment (deploy/base/worker/), from the same image as the API
with a different command. Same image on purpose: the pipeline, the models and the
DSPy configuration are all shared, and a separate image would let the two drift
apart while looking identical in the repo.

THREADING MODEL, which is the only genuinely awkward part of this file.
google-cloud-pubsub's streaming pull has no asyncio interface: `subscribe()`
dispatches each message to a callback on its own thread pool. The turn pipeline
is thoroughly async. So this module owns an event loop on the main thread, and
each callback thread hands its coroutine over with
`asyncio.run_coroutine_threadsafe` and blocks on the result before acking. That
block is deliberate — the callback thread's lifetime is what Pub/Sub's flow
control counts, so blocking it is what makes `max_messages` an actual
concurrency ceiling rather than a polite suggestion.

ACK SEMANTICS. Ack on success and on permanent failure; nack on transient
failure so it comes back. A turn that raised inside the pipeline is *not*
transient — run_turn already caught it, emitted an apology and terminated the
stream — so redelivering it would only spend more OpenRouter budget arriving at
the same answer. Only infrastructure failures nack.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.core.config import settings
from app.core.loadtest import load_test_scope
from app.core.logging import configure_logging
from app.core.metrics import start_exporter as start_metrics_exporter
from app.core.tracing import configure_tracing, shutdown_tracing
from app.core.turn_metrics import CHAT_BUFFERS_RETAINED
from app.schemas.chat import ChatMessage
from app.services import chat_buffer, turn_stream
from app.services.turn_runner import run_turn

configure_logging()

logger = logging.getLogger(__name__)

# Identifies which pod holds a turn claim. Only ever read by a human reading
# logs, but that is exactly the moment it matters.
WORKER_ID = os.getenv("HOSTNAME") or f"worker-{uuid.uuid4().hex[:8]}"


def _decode(
    message: Any,
) -> tuple[str, list[ChatMessage], dict | None, str | None, bool] | None:
    """Parse a Pub/Sub message into run_turn's arguments, or None if malformed.

    A malformed message is permanent: it will decode exactly as badly on every
    redelivery, so the caller acks it away rather than letting it cycle until the
    DLQ takes it.
    """
    import json

    try:
        payload = json.loads(message.data.decode("utf-8"))
        turn_id = payload["turn_id"]
        messages = [ChatMessage(**m) for m in payload["messages"]]
    except Exception:
        logger.exception("undecodable Pub/Sub message; discarding")
        return None
    return (
        turn_id,
        messages,
        payload.get("user"),
        payload.get("conversation_id"),
        # Absent on anything published before this field existed, and on every
        # real user's turn. Defaulting to False is the safe direction: the cost
        # of getting it wrong is a stubbed build shown to a real user.
        bool(payload.get("load_test")),
    )


async def _buffer_gauge_loop(interval_s: int = 60) -> None:
    """Keep `palladium_chat_buffers_retained` current.

    Sampled on a loop rather than updated at the point of retention, because the
    question it answers is "how many turns are unpersisted right now" — including
    ones retained by a worker that has since been replaced, which no in-process
    counter would know about.

    Every replica reports the same instance-wide number under its own pod label,
    so the alert in deploy/monitoring/ reduces with REDUCE_MAX rather than
    REDUCE_SUM; summing would multiply the count by the replica count.
    """
    while True:
        try:
            count = await chat_buffer.count_retained()
            if count is not None:
                CHAT_BUFFERS_RETAINED.set(count)
                if count:
                    logger.warning(
                        "%d chat buffer(s) awaiting persistence — these are turns "
                        "that did not reach Postgres (deploy/messaging.md §5)",
                        count,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never let a monitoring loop take down the worker it monitors.
            logger.exception("buffer gauge sample failed")
        await asyncio.sleep(interval_s)


async def _handle(
    turn_id: str,
    messages: list[ChatMessage],
    user: dict | None,
    conversation_id: str | None,
    load_test: bool = False,
) -> None:
    if not await turn_stream.claim(
        turn_id, WORKER_ID, settings.PUBSUB_ACK_EXTENSION_S * 2
    ):
        logger.info("turn %s already claimed; skipping duplicate delivery", turn_id)
        return

    try:
        # Wraps run_turn only, not the claim: the claim is bookkeeping that must
        # behave identically either way, and the scope exists solely to put the
        # LM chokepoints into stub mode for the duration of the pipeline.
        with load_test_scope(load_test):
            await run_turn(turn_id, messages, user, conversation_id)
    except BaseException:
        # Includes CancelledError from a SIGTERM mid-turn. Releasing the claim is
        # what lets the redelivery actually re-run the turn instead of being
        # skipped as a duplicate — without this, a rolling restart would silently
        # drop every turn that was in flight.
        await turn_stream.release_claim(turn_id)
        raise


class Worker:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._loop_thread: threading.Thread | None = None
        self._pull: Any = None
        self._subscriber: Any = None
        self._gauge_task: Any = None
        self._stopping = threading.Event()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        from google.cloud import pubsub_v1

        if not settings.PUBSUB_SUBSCRIPTION:
            raise RuntimeError("PUBSUB_SUBSCRIPTION is required to run the worker")

        # /metrics on the same port contract as the API, so the PodMonitoring in
        # deploy/overlays/prod/podmonitoring.yaml scrapes the worker with the
        # same config once its selector includes this Deployment.
        start_metrics_exporter()

        # Before any turn runs, so the first message off the subscription is
        # traced like every one after it.
        configure_tracing("palladium-worker")

        self._loop_thread = threading.Thread(
            target=self._run_loop, name="worker-loop", daemon=True
        )
        self._loop_thread.start()

        # Scheduled onto the worker loop from this thread, so it starts as soon
        # as the loop is running rather than waiting for a first message.
        self._gauge_task = asyncio.run_coroutine_threadsafe(
            _buffer_gauge_loop(), self._loop
        )

        from app.core.pubsub import _project_id  # deliberate: same resolution logic

        self._subscriber = pubsub_v1.SubscriberClient()
        path = self._subscriber.subscription_path(
            _project_id(), settings.PUBSUB_SUBSCRIPTION
        )

        flow = pubsub_v1.types.FlowControl(
            # The concurrency ceiling. Each in-flight message occupies a callback
            # thread blocked on a turn, so this is simultaneously the thread count
            # and the number of turns this pod runs at once.
            max_messages=settings.PUBSUB_MAX_CONCURRENCY,
            # Lease extension stops here. Past it the message is redelivered even
            # though this pod is still working on it — which the Valkey claim then
            # turns into a skip rather than a duplicate run.
            max_lease_duration=settings.PUBSUB_ACK_EXTENSION_S,
        )
        # Scheduler thread count must match max_messages, or messages are leased
        # (and their deadlines extended) while waiting for a free thread.
        scheduler = pubsub_v1.subscriber.scheduler.ThreadScheduler(
            executor=ThreadPoolExecutor(max_workers=settings.PUBSUB_MAX_CONCURRENCY)
        )

        self._pull = self._subscriber.subscribe(
            path, callback=self._callback, flow_control=flow, scheduler=scheduler
        )
        logger.info(
            "worker %s subscribed to %s (concurrency=%s)",
            WORKER_ID,
            path,
            settings.PUBSUB_MAX_CONCURRENCY,
        )

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def wait(self) -> None:
        assert self._pull is not None
        try:
            self._pull.result()
        except Exception:
            if not self._stopping.is_set():
                logger.exception("streaming pull failed")
                raise

    def stop(self) -> None:
        """Drain on SIGTERM: stop pulling, let in-flight turns finish, then exit.

        Order matters. Cancelling the pull first means no new turns start, so the
        wait below is bounded by the longest turn already running rather than
        being continually refreshed by new arrivals.
        """
        if self._stopping.is_set():
            return
        self._stopping.set()
        logger.info("worker %s draining", WORKER_ID)

        if self._pull is not None:
            self._pull.cancel()
            try:
                # terminationGracePeriodSeconds on the Deployment must exceed
                # this, or the kubelet SIGKILLs mid-drain and the turns this is
                # waiting for get redelivered anyway.
                self._pull.result(timeout=60)
            except Exception:
                logger.info(
                    "drain finished with turns still in flight; they will redeliver"
                )

        if self._gauge_task is not None:
            self._gauge_task.cancel()

        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=10)

        from app.core.valkey import close_client

        # New loop: the worker loop is stopped by now, and closing the Valkey
        # pool still needs to await.
        asyncio.run(close_client())

        # After the drain, so the turns that just finished are included. This
        # is the flush that matters: turns are short, spans are batched, and a
        # rollout SIGTERMs this process without warning — without it the last
        # turn before every deploy vanishes from both backends.
        shutdown_tracing()

    # -- message handling --------------------------------------------------

    def _callback(self, message: Any) -> None:
        decoded = _decode(message)
        if decoded is None:
            message.ack()  # permanent; see _decode
            return
        turn_id, messages, user, conversation_id, load_test = decoded

        future = asyncio.run_coroutine_threadsafe(
            _handle(turn_id, messages, user, conversation_id, load_test), self._loop
        )
        try:
            future.result()
        except Exception:
            # Infrastructure failure — run_turn handles pipeline errors itself
            # and returns normally, so reaching here means something below it
            # broke. Worth a redelivery.
            logger.exception("turn %s failed; nacking for redelivery", turn_id)
            message.nack()
            return

        message.ack()


def main() -> None:
    worker = Worker()

    def _on_signal(signum: int, _frame: Any) -> None:
        logger.info("received %s", signal.Signals(signum).name)
        worker.stop()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    worker.start()
    worker.wait()


if __name__ == "__main__":
    main()
