"""A build pipeline stopped at the case step, waiting for the user to pick.

WHY THIS IS A TABLE AND NOT JUST A VALKEY KEY. The pause is open-ended: the
user may pick in four seconds or come back tomorrow, and between those two the
turn that produced the options has ended, its worker has moved on, and the pod
that ran it may well have been replaced. Valkey holds the fast copy and answers
almost every resume, but it is a cache — it evicts under pressure and it
expires — and losing this payload does not cost a cache miss, it costs nine
LLM calls and the user's whole build. So Postgres carries the durable copy and
Valkey is the read-through in front of it, the same division of labour
`conversations.graph_checkpoint` already keeps against Valkey's checkpointer.

`resumed_at` is what makes the resume happen at most once. Claiming is a
conditional UPDATE (see services/paused_build.py), so two picks racing — a
double click, a redelivered message — resolve to one winner in one statement,
with no lock held across the pipeline work that follows.

`conversation_id` is a plain column rather than a foreign key for the same
reason shared_builds' is: the row is written mid-turn, before the conversation
is necessarily in Postgres, and guest builds never get a conversation at all.
"""

import uuid

from sqlalchemy import Column, DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func

from app.db.base import Base


class PausedBuild(Base):
    __tablename__ = "paused_builds"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # The pick token, minted as the pipeline's session id. Handed to the client
    # inside the case_options payload; knowing it is what authorizes a resume.
    token = Column(String(64), nullable=False, unique=True, index=True)

    # Everything needed to finish the build: the serialized DSPyBuildState, the
    # telemetry recorder mid-flight, the profile, and the conversation as it
    # read when the pause happened. See services/paused_build.py for the shape.
    payload = Column(JSONB, nullable=False)

    conversation_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    # Set by the winning claim. Non-null means this build has already been
    # resumed (or is being resumed right now) and must not be started again.
    resumed_at = Column(DateTime(timezone=True), nullable=True)
