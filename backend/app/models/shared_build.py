"""A publicly shareable snapshot of a generated build.

WHY A SNAPSHOT AND NOT A JOIN. The build a conversation produced lives in
message metadata, which is private to the conversation's owner and shaped by
whatever the pipeline emitted that day. A share link has the opposite needs:
readable by anyone holding the token, and frozen — the page and the PDF someone
shares must keep saying what they said when they were shared, even after
catalogs, prices, or the pipeline move on. So the row copies the build payload
outright rather than referencing anything that can drift.

The token is the only credential: unguessable (secrets.token_urlsafe), carried
in the URL, and granting access to nothing but this snapshot. `conversation_id`
is a plain column, not a foreign key, because the row is written mid-turn —
before _save_turn has necessarily created the conversation — and for guest
turns the conversation never comes to exist at all.
"""

import secrets
import uuid

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.db.base import Base


def new_share_token() -> str:
    # 12 random bytes -> 16 url-safe chars. Comfortably unguessable for a
    # capability URL while staying short enough to read aloud.
    return secrets.token_urlsafe(12)


class SharedBuild(Base):
    __tablename__ = "shared_builds"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    token = Column(
        String(32), nullable=False, unique=True, index=True, default=new_share_token
    )

    # The full build payload as emitted to the client (label, description,
    # total_approx, parts). The `profile` key is stripped before insert — it
    # paraphrases what the user told the intake chat, which is not something a
    # share link should republish.
    build = Column(JSONB, nullable=False)

    # Which resolution produced it ("custom_dspy" or a reference build key).
    build_key = Column(String(100), nullable=True)

    conversation_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
