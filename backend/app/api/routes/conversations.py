from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.auth import verify_firebase_token
from app.core.db import get_async_db
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.schemas.chat import ConversationDetail, ConversationSummary, MessageOut

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
            )
            for m in conversation.messages
        ],
    )
