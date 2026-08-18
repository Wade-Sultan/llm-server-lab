from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Annotated, Any

from assistant_stream import RunController, create_run
from assistant_stream.serialization import DataStreamResponse
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.core import pubsub
from app.core.auth import optional_firebase_token
from app.core.loadtest import is_load_test
from app.core.valkey import is_available as valkey_available
from app.services import transport, turn_stream
from app.services.turn_runner import run_turn

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

# Strong refs to in-flight fallback turns. asyncio only holds weak references to
# tasks, so without this a turn can be collected mid-run under memory pressure.
_background_turns: set[asyncio.Task] = set()

# Comment frames on an otherwise silent connection. The DSPy build can run for
# minutes without emitting anything, and the GKE Gateway reaps an idle
# connection long before that — this is what the hand-written `: ping` in the
# old SSE relay was for, now handled by the encoder.
_HEARTBEAT_S = 15.0


class ChatRequest(BaseModel):
    """The assistant-transport request body.

    Only the fields this server reads are declared; the runtime also sends
    `system`, `tools` and call settings, which are accepted and ignored rather
    than rejected — a client that adds a field should not start failing.
    """

    model_config = {"extra": "allow"}

    commands: list[dict[str, Any]] = Field(default_factory=list)
    state: Any = None
    # The conversation this turn belongs to, passed through the runtime's `body`
    # option. Distinct from assistant-ui's own `threadId`, which tracks its
    # thread list rather than our Conversation rows.
    conversation_id: str | None = None
    # The message the new one is appended after. This is what makes an edit an
    # edit — see `rewind_prefix` in app/services/transport.py.
    #
    # `None` is a real value here (the first message was edited, so nothing
    # survives) and is NOT the same as the field being absent, which is why the
    # route tests `model_fields_set` rather than `parent_id is None`. Reading
    # them as the same thing would let a request that never mentioned a parent
    # delete an entire conversation.
    #
    # IGNORE THE WARNING THIS PRODUCES. Building the route makes pydantic 2.12
    # emit `UnsupportedFieldAttributeWarning: The 'alias' attribute with value
    # 'parentId' ... has no effect in the context it was used`. It is wrong —
    # the alias does apply, and tests/test_chat_edit_rewind.py pins that through
    # real HTTP requests precisely so nobody "fixes" it by reading the warning.
    # `Annotated` is the spelling the warning recommends and it warns anyway.
    parent_id: Annotated[str | None, Field(alias="parentId")] = None


def _stream_response(callback, state: Any) -> DataStreamResponse:
    return DataStreamResponse(create_run(callback, state=state), heartbeat=_HEARTBEAT_S)


async def _dispatch(
    turn_id: str,
    messages: list,
    user: dict | None,
    conversation_id: str | None,
    rewound: bool = False,
    case_pick: tuple[str, str] | None = None,
) -> bool:
    """Hand the turn to a worker. False means it must run in this process.

    Unchanged from the pre-transport implementation, deliberately: this is the
    control plane, and moving the browser onto assistant-transport was never a
    reason to touch it. Reachability, not configuration — a pod that cannot read
    Valkey must not publish to a worker it then cannot hear back from.
    """
    if not (await valkey_available() and pubsub.is_enabled()):
        return False

    dispatched = await pubsub.publish_turn(
        turn_id,
        conversation_id,
        {
            "turn_id": turn_id,
            "conversation_id": conversation_id,
            "user": user,
            "messages": [m.model_dump() for m in messages],
            # Carries load-test mode across the process boundary. The
            # middleware's ContextVar stops at this pod, and the worker is where
            # every LM call actually happens. See core/loadtest.py.
            "load_test": is_load_test(),
            # `messages` is a rewritten history, not a longer one — the worker
            # has to delete the rows this edit replaced rather than append to
            # them. Only this pod can know: the evidence is `parentId`, which
            # never leaves the request.
            "rewound": rewound,
            # [token, case_name] when this turn finishes a build that paused at
            # the case step, rather than answering something the user typed.
            "case_pick": list(case_pick) if case_pick else None,
        },
    )
    if dispatched:
        # The fast half of the worker pool's wake-up. Pub/Sub is the queue; this
        # is the same fact written somewhere KEDA can read without waiting on
        # Cloud Monitoring's 60-180s metric delay. Only on the dispatched path —
        # the inline and in-process fallbacks below need no worker, so waking
        # one would start a pod to do nothing. See turn_stream.push_wake.
        await turn_stream.push_wake(turn_id)
    return dispatched


def _case_pick(commands: list[dict]) -> tuple[str, str] | None:
    """The case the user clicked, if this request carries a pick.

    `select-case` is a custom assistant-transport command (declared on the
    client in types/assistant-ui.d.ts). It rides the ordinary /chat request
    rather than a side channel of its own, which is what lets a pick reuse the
    whole turn machinery — dispatch, the Valkey event stream, resume-on-reload
    — instead of reimplementing it.

    Last one wins, matching how the command queue would apply them in order.
    """
    found: tuple[str, str] | None = None
    for command in commands or []:
        if not isinstance(command, dict) or command.get("type") != "select-case":
            continue
        token, case_name = command.get("token"), command.get("caseName")
        if (
            isinstance(token, str)
            and isinstance(case_name, str)
            and token
            and case_name
        ):
            found = (token, case_name)
    return found


@router.post("/chat")
async def chat(
    req: ChatRequest, user: dict | None = Depends(optional_firebase_token)
) -> Response:
    """Run one turn, streaming its state back over assistant-transport.

    HOW THE TURN ACTUALLY RUNS IS UNCHANGED. When Pub/Sub and Valkey are both
    reachable the turn goes to a worker and this response merely reads its
    Valkey stream — so the turn survives this connection dying and a reconnect
    can pick it up mid-build (see /chat/resume). Otherwise it runs inline and
    dies with the request, which is the local-development path.
    """
    # The base handed to create_run must stay exactly what the client sent —
    # operations are deltas applied on top of the client's own copy, so mutating
    # it here would misalign every message index. This turn's new messages are
    # appended inside the run instead, which is also what keeps them on screen:
    # the runtime drops its optimistic echo as soon as the first operation lands.
    state = transport.ensure_shape(req.state)
    pending = transport.command_messages(req.commands)

    # An edit rewrites history: the edited message and every turn that followed
    # from it are discarded, and the pipeline must see the conversation as it now
    # reads rather than as it was. Applied to `state` only inside the run (see
    # `_rewind`), so the base stays exactly what the client POSTed.
    # `model_extra` is not redundant with `model_fields_set`. Because `extra` is
    # "allow", a body carrying the *unaliased* key `parent_id` files it under
    # extras AND adds that name to `model_fields_set`, while `req.parent_id` —
    # which only `parentId` populates — stays None. Testing set-ness alone would
    # read such a body as "parent is null" and wipe the conversation, the exact
    # loss the absent-versus-null distinction above exists to prevent.
    keep = (
        transport.rewind_prefix(state["messages"], req.parent_id)
        if "parent_id" in req.model_fields_set
        and "parent_id" not in (req.model_extra or {})
        else None
    )
    history = state["messages"] if keep is None else keep

    messages = transport.to_chat_messages(history + pending)
    # A case pick is a click on a card, not something the user said, so it
    # arrives with no message of its own — and the turn it starts is still a
    # real turn. Only a request carrying neither has nothing to respond to.
    case_pick = _case_pick(req.commands)
    if not messages and case_pick is None:
        raise HTTPException(status_code=400, detail="No message to respond to.")

    # One id per turn, minted here rather than derived from conversation_id: a
    # conversation has many turns and they each need their own event stream.
    turn_id = uuid.uuid4().hex
    conversation_id = req.conversation_id

    dispatched = await _dispatch(
        turn_id,
        messages,
        user,
        conversation_id,
        rewound=keep is not None,
        case_pick=case_pick,
    )

    if not dispatched and await valkey_available():
        # Valkey but no Pub/Sub: still worth going through the stream, because
        # resumption works and this pod is no longer the only place the events
        # exist. Not awaited — the callback below consumes what it writes.
        task = asyncio.create_task(
            run_turn(
                turn_id,
                messages,
                user,
                conversation_id,
                rewound=keep is not None,
                case_pick=case_pick,
            )
        )
        _background_turns.add(task)
        task.add_done_callback(_background_turns.discard)
        dispatched = True

    if dispatched:
        # Recorded before streaming starts, so a browser that reloads two
        # seconds in can already find the turn to reattach to.
        if conversation_id:
            await turn_stream.set_active_turn(conversation_id, turn_id)

        async def run_callback(controller: RunController) -> None:
            await transport.stream_turn_into(
                controller, turn_id, pending=pending, keep=keep
            )

        return _stream_response(run_callback, state)

    async def inline_callback(controller: RunController) -> None:
        await transport.run_turn_inline_into(
            controller,
            messages,
            conversation_id,
            pending=pending,
            keep=keep,
            case_pick=case_pick,
        )

    return _stream_response(inline_callback, state)


class ResumeRequest(BaseModel):
    model_config = {"extra": "allow"}

    state: Any = None
    conversation_id: str | None = None


@router.post("/chat/resume")
async def chat_resume(req: ResumeRequest) -> Response:
    """Reattach to the turn currently running for a conversation.

    Deliberately unauthenticated, and safe for the same reason the old
    `/chat/{turn_id}/stream` was: nothing here is returned that the client did
    not already have, and reattaching requires knowing the conversation id.
    Requiring a token would break the case this exists for — a Firebase ID token
    lives an hour, and a tab backgrounded long enough to drop its connection may
    well come back with an expired one.

    The client sends no run id (assistant-transport has none to send), so the
    server resolves it from the conversation. Replay is from the beginning
    because state snapshots are absolute — see app/services/transport.py.
    """
    if not req.conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id is required.")

    if not await valkey_available():
        # Nothing to resume: without Valkey the turn ran inline and died with
        # the connection that started it.
        raise HTTPException(
            status_code=404, detail="No resumable turn (Valkey is unreachable)."
        )

    turn_id = await turn_stream.get_active_turn(req.conversation_id)
    if turn_id is None or not await turn_stream.exists(turn_id):
        # Either nothing is running, or the stream aged out past
        # TURN_STREAM_TTL_S. Indistinguishable from here, and the client's
        # response to both is the same: stop trying to resume.
        raise HTTPException(status_code=404, detail="No turn to resume.")

    async def run_callback(controller: RunController) -> None:
        # `resuming` so the replay reuses the half-finished assistant message the
        # client already has rather than stacking a second one above it.
        await transport.stream_turn_into(controller, turn_id, resuming=True)

    return _stream_response(run_callback, transport.ensure_shape(req.state))
