from __future__ import annotations

import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core import pubsub
from app.core.auth import optional_firebase_token
from app.core.loadtest import is_load_test
from app.core.valkey import is_available as valkey_available
from app.schemas.chat import ChatMessage, ChatRequest
from app.services import turn_stream
from app.services.chat_pipeline import run_chat_turn
from app.services.turn_runner import run_turn

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# SSE plumbing. `X-Accel-Buffering` disables proxy buffering; without it a
# reverse proxy can hold tokens back until its buffer fills, which reads to the
# user as a long stall followed by a wall of text.
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

_INTERNAL_EVENTS = frozenset({"usage", "reference_estimate"})

# Strong refs to in-flight fallback turns. asyncio only holds weak references to
# tasks, so without this a turn can be collected mid-run under memory pressure.
_background_turns: set[asyncio.Task] = set()


def _sse(event: dict, entry_id: str | None = None) -> str:
    """Format one SSE frame.

    The `id:` line is what makes resumption work: it is echoed back by the client
    as Last-Event-ID, and it is the Valkey stream entry ID, so the server can
    resume from exactly that point with no translation layer in between.
    """
    payload = json.dumps(event, default=str)
    if entry_id is None:
        return f"data: {payload}\n\n"
    return f"id: {entry_id}\ndata: {payload}\n\n"


async def _relay(turn_id: str, last_id: str = "0"):
    """Relay a turn's Valkey stream to the client as SSE."""
    # Sent before anything else so a client that loses the connection knows which
    # turn to reconnect to. Without it, a disconnect during the very first
    # progress event would leave the browser with no handle on the running turn.
    yield _sse({"type": "turn", "turn_id": turn_id})

    # An unreachable Valkey ends tail() immediately, which would otherwise send
    # [DONE] with no content — a blank reply, no error, and nothing in any log
    # to explain it, while the worker completes the turn perfectly. Refuse to
    # fail that quietly.
    if not await valkey_available():
        logger.error(
            "relay for turn %s cannot reach Valkey; the worker may still be "
            "running this turn but its events are unreadable from this pod",
            turn_id,
        )
        yield _sse(
            {
                "type": "token",
                "text": "\n\nLost the connection to the build service. Please try again.",
            }
        )
        yield "data: [DONE]\n\n"
        return

    saw_event = False
    async for item in turn_stream.tail(turn_id, last_id=last_id):
        if item is None:
            # Comment frame. Keeps the connection warm through the Gateway's idle
            # timeout during the long silent stretch while DSPy runs.
            yield ": ping\n\n"
            continue
        entry_id, event = item
        saw_event = True
        yield _sse(event, entry_id)

    # tail() also returns when it gives up waiting for a terminal entry, which
    # means the worker died mid-turn (or never picked it up). Distinguishable
    # from a normal finish only by having produced nothing.
    if not saw_event:
        logger.warning(
            "relay for turn %s produced no events — no worker wrote to this "
            "stream. Check that the worker Deployment is running and that both "
            "pods point at the same Valkey.",
            turn_id,
        )
        yield _sse(
            {
                "type": "token",
                "text": "\n\nThat build didn't start. Please try again.",
            }
        )

    yield "data: [DONE]\n\n"


async def _inline_stream(
    messages: list[ChatMessage], user: dict | None, conversation_id: str | None
):
    """Run the turn in-request and stream it directly, with no Valkey involved.

    The fallback for local development and for any deployment where Valkey is not
    configured. It is the pre-Pub/Sub behaviour, kept verbatim in one respect that
    matters: the turn dies with the connection. That is precisely the weakness the
    dispatched path exists to fix, so this must never be the production path — the
    startup log in app/core/valkey.py says so out loud when it engages.
    """
    from app.services.turn_runner import save_turn

    assistant_text = ""
    turn_usage: dict | None = None
    reached_recommendation = False
    build_data: dict | None = None
    build_key: str | None = None
    ref_estimate_data: dict | None = None
    ref_estimate_key: str | None = None

    try:
        async for event in run_chat_turn(messages, conversation_id=conversation_id):
            etype = event.get("type")
            if etype == "token":
                assistant_text += event.get("text", "")
            elif etype == "build":
                reached_recommendation = True
                build_data = event.get("data")
                build_key = event.get("key")
            elif etype == "reference_estimate":
                ref_estimate_data = event.get("data")
                ref_estimate_key = event.get("key")
            elif etype == "usage":
                turn_usage = event

            if etype not in _INTERNAL_EVENTS:
                yield _sse(event)
    except Exception:
        logger.exception("Chat pipeline error")
        yield _sse(
            {
                "type": "token",
                "text": "\n\nSomething went wrong generating your recommendation. Please try again.",
            }
        )

    if user and conversation_id:
        await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: save_turn(
                user.get("uid", ""),
                user.get("email"),
                conversation_id,
                messages,
                assistant_text,
                turn_usage,
                reached_recommendation,
                build_data,
                build_key,
                ref_estimate_data,
                ref_estimate_key,
            ),
        )

    yield "data: [DONE]\n\n"


@router.post("/chat")
async def chat(
    req: ChatRequest, user: dict | None = Depends(optional_firebase_token)
) -> StreamingResponse:
    """
    Stream a recommendation response for the given conversation.

    Accessible to both authenticated users and guests. Authenticated users'
    conversations are persisted when a conversation_id is supplied.

    The response is SSE either way, but how it is produced differs. When Pub/Sub
    and Valkey are both configured the turn is dispatched to a worker and this
    response merely relays its Valkey stream — so the turn survives this
    connection dying, and the client can reconnect to /chat/{turn_id}/stream and
    pick up mid-build. Otherwise the turn runs inline and dies with the request.
    """
    # One id per turn, minted here rather than derived from conversation_id: a
    # conversation has many turns, and they each need their own event stream and
    # their own claim.
    turn_id = uuid.uuid4().hex

    # Reachability, not configuration. A builder pod that cannot read Valkey
    # must not hand the turn to a worker it then cannot hear back from — see
    # app/core/valkey.py::is_available.
    valkey_up = await valkey_available()

    dispatched = False
    if valkey_up and pubsub.is_enabled():
        dispatched = await pubsub.publish_turn(
            turn_id,
            req.conversation_id,
            {
                "turn_id": turn_id,
                "conversation_id": req.conversation_id,
                "user": user,
                "messages": [m.model_dump() for m in req.messages],
                # Carries load-test mode across the process boundary. The
                # middleware's ContextVar stops at this pod, and the worker is
                # where every LM call actually happens — so without this line a
                # load test against production stubs nothing and bills for one
                # full build per simulated user. See core/loadtest.py.
                "load_test": is_load_test(),
            },
        )

    if dispatched:
        return StreamingResponse(
            _relay(turn_id), media_type="text/event-stream", headers=_SSE_HEADERS
        )

    if valkey_up:
        # Valkey but no Pub/Sub: still worth running through the stream, because
        # resumption works and the pod is no longer the only place the events
        # exist. The task is not awaited here — _relay consumes what it writes.
        #
        # Held on the app state so a shutdown can await it; a bare create_task
        # would be garbage-collected mid-turn if nothing kept a reference.
        task = asyncio.create_task(
            run_turn(turn_id, req.messages, user, req.conversation_id)
        )
        _background_turns.add(task)
        task.add_done_callback(_background_turns.discard)
        return StreamingResponse(
            _relay(turn_id), media_type="text/event-stream", headers=_SSE_HEADERS
        )

    return StreamingResponse(
        _inline_stream(req.messages, user, req.conversation_id),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get("/chat/{turn_id}/stream")
async def chat_stream(
    turn_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """Resume an in-flight turn's event stream.

    Deliberately unauthenticated, and safe because turn_id is an unguessable
    128-bit value that is only ever returned to the client that started the turn.
    Requiring a token here would break the case this exists for: a Firebase ID
    token lives an hour, and a tab that was backgrounded long enough to drop its
    connection may well come back with an expired one.

    `resume` in the query string overrides the Last-Event-ID header, for clients
    using fetch() rather than EventSource — fetch has no automatic Last-Event-ID
    handling, and the frontend adapter tracks the id itself.
    """
    if not await valkey_available():
        raise HTTPException(
            status_code=503,
            detail="Turn streams are not available (Valkey is unreachable).",
        )

    resume_from = request.query_params.get("resume") or last_event_id or "0"

    if not await turn_stream.exists(turn_id):
        # Either the turn id is wrong, or its stream aged out past
        # TURN_STREAM_TTL_S. Indistinguishable from here, and the client's
        # response to both is the same: start a new turn.
        raise HTTPException(status_code=404, detail="No such turn, or it has expired.")

    return StreamingResponse(
        _relay(turn_id, last_id=resume_from),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
