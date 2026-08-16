from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import verify_firebase_token
from app.core.db import get_async_db
from app.models.build_feedback import BuildFeedback, FeedbackRating
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.reference_build import ReferenceBuild
from app.models.user import User
from app.schemas.chat import (
    ConversationDetail,
    ConversationSummary,
    FeedbackIn,
    FeedbackOut,
    MessageOut,
)

router = APIRouter(tags=["conversations"])


@router.get("/conversations", response_model=list[ConversationSummary])
async def get_conversations(
    user: dict = Depends(verify_firebase_token),
    db: AsyncSession = Depends(get_async_db),
    skip: int = 0,
    limit: int = 50,
) -> list[ConversationSummary]:
    """Return the authenticated user's conversation history, newest first."""
    firebase_uid = user.get("uid")

    db_user_result = await db.execute(
        select(User).where(User.firebase_uid == firebase_uid)
    )
    db_user = db_user_result.scalar_one_or_none()

    if not db_user:
        return []

    rows_result = await db.execute(
        select(
            Conversation.id,
            Conversation.title,
            Conversation.created_at,
            Conversation.updated_at,
            func.count(Message.id).label("message_count"),
        )
        .outerjoin(Message, Message.conversation_id == Conversation.id)
        .where(Conversation.user_id == db_user.id)
        .group_by(Conversation.id)
        .order_by(Conversation.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    rows = rows_result.all()

    return [
        ConversationSummary(
            id=row.id,
            title=row.title,
            created_at=row.created_at,
            updated_at=row.updated_at,
            message_count=row.message_count,
        )
        for row in rows
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    user: dict = Depends(verify_firebase_token),
    db: AsyncSession = Depends(get_async_db),
) -> ConversationDetail:
    """Return a single conversation with its messages."""
    firebase_uid = user.get("uid")

    db_user_result = await db.execute(
        select(User).where(User.firebase_uid == firebase_uid)
    )
    db_user = db_user_result.scalar_one_or_none()

    if not db_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    conversation_result = await db.execute(
        select(Conversation)
        .options(selectinload(Conversation.messages))
        .where(Conversation.id == conversation_id)
    )
    conversation = conversation_result.scalar_one_or_none()
    if not conversation or conversation.user_id != db_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    feedback_row = (
        await db.execute(
            select(BuildFeedback).where(
                BuildFeedback.conversation_id == conversation.id,
                BuildFeedback.user_id == db_user.id,
            )
        )
    ).scalar_one_or_none()

    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        created_at=conversation.created_at,
        messages=[
            MessageOut(
                id=m.id,
                role=m.role,
                content=m.content,
                created_at=m.created_at,
                metadata=m.metadata_,
            )
            for m in conversation.messages
        ],
        feedback=(
            FeedbackOut(
                rating=feedback_row.rating.value, build_id=feedback_row.build_id
            )
            if feedback_row
            else None
        ),
    )


async def _own_conversation(
    conversation_id: uuid.UUID, firebase_uid: str | None, db: AsyncSession
) -> tuple[Conversation, User]:
    """Resolve a conversation the caller owns, or 404.

    Deliberately 404 rather than 403 on someone else's conversation: a 403
    confirms the id exists, which is the one bit of information an enumerating
    caller wants.
    """
    db_user = (
        await db.execute(select(User).where(User.firebase_uid == firebase_uid))
    ).scalar_one_or_none()
    if not db_user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    conversation = (
        await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one_or_none()
    if not conversation or conversation.user_id != db_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    return conversation, db_user


@router.put(
    "/conversations/{conversation_id}/feedback",
    response_model=FeedbackOut,
)
async def set_feedback(
    conversation_id: uuid.UUID,
    body: FeedbackIn,
    user: dict = Depends(verify_firebase_token),
    db: AsyncSession = Depends(get_async_db),
) -> FeedbackOut:
    """Record — or change — this user's thumbs up/down on a conversation's build.

    PUT rather than POST because it is idempotent and there is at most one of
    these per user per conversation: clicking thumbs-down twice leaves the same
    single row, and switching from up to down updates it in place. That is what
    makes a count of `down` rows mean "people who dislike this" rather than
    "clicks logged".
    """
    conversation, db_user = await _own_conversation(
        conversation_id, user.get("uid"), db
    )

    # Resolve the client's build_key to a concrete pc_builds row. Falls back to
    # the conversation's own build_id when no key was sent (or the key is not a
    # reference build), which keeps the rating attributable even then — a null
    # build_id only costs the aggregate view, never the vote itself.
    build_id = conversation.build_id
    if body.build_key:
        ref_build = (
            await db.execute(
                select(ReferenceBuild).where(ReferenceBuild.build_key == body.build_key)
            )
        ).scalar_one_or_none()
        if ref_build and ref_build.pc_build_id:
            build_id = ref_build.pc_build_id

    existing = (
        await db.execute(
            select(BuildFeedback).where(
                BuildFeedback.conversation_id == conversation.id,
                BuildFeedback.user_id == db_user.id,
            )
        )
    ).scalar_one_or_none()

    if existing:
        existing.rating = FeedbackRating(body.rating)
        existing.build_id = build_id
    else:
        db.add(
            BuildFeedback(
                conversation_id=conversation.id,
                build_id=build_id,
                user_id=db_user.id,
                rating=FeedbackRating(body.rating),
            )
        )

    await db.commit()
    return FeedbackOut(rating=body.rating, build_id=build_id)


@router.delete(
    "/conversations/{conversation_id}/feedback",
    status_code=status.HTTP_204_NO_CONTENT,
    # Both are needed. `-> None` does NOT mean "no response model" to FastAPI:
    # the annotation resolves to NoneType, which is a class and therefore
    # truthy, so the route refuses to build at import time with "Status code
    # 204 must not have a response body". response_model=None says it outright,
    # and response_class stops a JSON body being serialized into a 204.
    response_model=None,
    response_class=Response,
)
async def clear_feedback(
    conversation_id: uuid.UUID,
    user: dict = Depends(verify_firebase_token),
    db: AsyncSession = Depends(get_async_db),
) -> None:
    """Withdraw a rating — what clicking an already-lit thumb does.

    Deleting rather than storing a third "neutral" state: "rated then changed
    their mind" and "never rated" are the same thing to every question this
    table answers, and a null state would have to be excluded from every count.
    """
    conversation, db_user = await _own_conversation(
        conversation_id, user.get("uid"), db
    )

    await db.execute(
        delete(BuildFeedback).where(
            BuildFeedback.conversation_id == conversation.id,
            BuildFeedback.user_id == db_user.id,
        )
    )
    await db.commit()
