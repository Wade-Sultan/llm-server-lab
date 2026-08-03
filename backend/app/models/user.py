import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )

    firebase_uid = Column(
        String(128),
        unique=True,
        nullable=True,  # nullable during migration; tighten to False later
        index=True,
        comment="Firebase Auth UID",
    )

    email = Column(String(255), unique=True, index=True, nullable=False)
    username = Column(String(255), nullable=True)

    hashed_password = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    is_superuser = Column(
        Boolean, nullable=False, default=False, server_default="false"
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

    conversations = relationship(
        "Conversation",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    @property
    def full_name(self) -> str | None:
        return self.username

    @full_name.setter
    def full_name(self, value: str | None) -> None:
        self.username = value
