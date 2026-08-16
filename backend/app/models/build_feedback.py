"""Thumbs up/down on a recommended build.

KEYED ON THE CONVERSATION, NOT THE BUILD, and the distinction is the whole
reason this table has two foreign keys instead of one.

`pc_builds` rows are shared templates. A recommendation resolves a build_key
through `reference_builds` to an existing pc_build row (see turn_runner's
"Link the conversation to the concrete PCBuild row" block), so every
conversation that lands on the same build points at the SAME pc_builds id.
Keyed on build alone, two users rating their own recommendations would look
like two votes on one object, with nothing left to say which conversation
either came from.

So `conversation_id` carries the identity — one vote per user per conversation,
changeable — and `build_id` is denormalized alongside it to make the aggregate
question ("which template scores worst across everyone who got it?") a group-by
rather than a join through a column that can drift.

WHY build_id IS COPIED RATHER THAN JOINED. `conversations.build_id` is written
once, on the first turn that produces a build, and never updated. A conversation
that gets edited and rewound onto a different recommendation keeps the original
value, so joining through it would attribute the vote to a build the user may
never have seen. This column records the build that was on screen when the
thumb was clicked.
"""

import enum
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class FeedbackRating(str, enum.Enum):
    UP = "up"
    DOWN = "down"


class BuildFeedback(Base):
    __tablename__ = "build_feedback"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        doc="The conversation whose recommendation was rated",
    )

    # SET NULL rather than CASCADE: deleting a build template must not delete
    # the record that someone disliked it. The rating still means something
    # attached to its conversation.
    build_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_builds.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        doc="The pc_builds row on screen when this was rated; see module docstring",
    )

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    rating = Column(
        Enum(
            FeedbackRating,
            name="feedback_rating",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        # One standing opinion per user per conversation. Changing your mind
        # updates this row rather than appending a second vote, which is what
        # lets the UI show the current state and lets a count of `down` rows
        # mean "people who currently dislike it" instead of "clicks".
        UniqueConstraint(
            "conversation_id", "user_id", name="uq_build_feedback_conversation_user"
        ),
    )

    conversation = relationship("Conversation")
    user = relationship("User")
