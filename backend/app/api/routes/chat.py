from __future__ import annotations

import asyncio
import functools
import json
import logging
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select

from app.schemas.chat import ChatMessage, ChatRequest
from app.services.chat_pipeline import run_chat_turn
from app.core.auth import optional_firebase_token

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


def _save_turn(
    firebase_uid: str,
    firebase_email: str | None,
    conversation_id: str,
    messages: list[ChatMessage],
    assistant_text: str,
) -> None:
    """Persist this chat turn to the DB. Runs in a thread executor (sync SQLAlchemy)."""
    from app.core.db import SessionLocal
    from app.models.conversation import Conversation
    from app.models.message import Message
    from app.models.user import User

    db = SessionLocal()
    try:
        # Get or create the DB user record for this Firebase user
        user = db.execute(
            select(User).where(User.firebase_uid == firebase_uid)
        ).scalar_one_or_none()

        if not user and firebase_email:
            # Try to match by email (user may have registered via the DB signup flow)
            user = db.execute(
                select(User).where(User.email == firebase_email)
            ).scalar_one_or_none()
            if user:
                user.firebase_uid = firebase_uid
                db.add(user)
                db.flush()

        if not user and firebase_email:
            # Auto-provision a record for Firebase-only users
            user = User(
                email=firebase_email,
                firebase_uid=firebase_uid,
                hashed_password="!firebase_oauth",
            )
            db.add(user)
            db.flush()

        if not user:
            logger.warning("Could not resolve DB user for firebase_uid=%s; skipping save", firebase_uid)
            return

        # Get or create the Conversation row
        conv_uuid = uuid.UUID(conversation_id)
        conversation = db.get(Conversation, conv_uuid)
        if not conversation:
            title = messages[0].content[:100] if messages else "New Build"
            conversation = Conversation(
                id=conv_uuid,
                user_id=user.id,
                title=title,
            )
            db.add(conversation)
            db.flush()

        # Count already-saved messages so we only append new ones
        saved_count = db.execute(
            select(func.count(Message.id)).where(Message.conversation_id == conv_uuid)
        ).scalar_one()

        for msg in messages[saved_count:]:
            db.add(Message(
                conversation_id=conv_uuid,
                role=msg.role,
                content=msg.content,
            ))

        if assistant_text:
            db.add(Message(
                conversation_id=conv_uuid,
                role="assistant",
                content=assistant_text,
            ))

        db.commit()
    except Exception:
        logger.exception("Failed to save conversation turn")
        db.rollback()
    finally:
        db.close()


async def _event_stream(messages: list[ChatMessage], user: dict | None, conversation_id: str | None):
    """Async generator that yields SSE-formatted lines."""
    assistant_text = ""
    try:
        async for event in run_chat_turn(messages):
            if event.get("type") == "token":
                assistant_text += event.get("text", "")
            # default=str guards against non-JSON-native types (UUID, Decimal,
            # datetime, ...) slipping into an event payload and killing the stream.
            line = f"data: {json.dumps(event, default=str)}\n\n"
            yield line
    except Exception:
        logger.exception("Chat pipeline error")
        error_event = json.dumps({
            "type": "token",
            "text": "\n\nSomething went wrong generating your recommendation. Please try again.",
        })
        yield f"data: {error_event}\n\n"

    # Persist the turn for authenticated users
    if user and conversation_id:
        save_fn = functools.partial(
            _save_turn,
            user.get("uid", ""),
            user.get("email"),
            conversation_id,
            messages,
            assistant_text,
        )
        await asyncio.get_running_loop().run_in_executor(None, save_fn)

    yield "data: [DONE]\n\n"


@router.post("/chat")
async def chat(req: ChatRequest, user: dict | None = Depends(optional_firebase_token)) -> StreamingResponse:
    """
    Stream a recommendation response for the given conversation.

    Accessible to both authenticated users and guests. Authenticated users'
    conversations are persisted when a conversation_id is supplied.
    """
    return StreamingResponse(
        _event_stream(req.messages, user, req.conversation_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering if proxied
        },
    )
