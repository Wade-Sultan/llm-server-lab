import uuid
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base import Base


class ReferenceBuild(Base):
    __tablename__ = "reference_builds"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    build_key   = Column(String(64), nullable=False, unique=True, index=True)
    label       = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    total_approx = Column(Integer, nullable=False)
    is_active   = Column(Boolean, nullable=False, server_default="true")
    created_at  = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), nullable=False,
                         server_default=func.now(), onupdate=func.now())

    pc_build_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_builds.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )

    parts = relationship(
        "ReferenceBuildPart", back_populates="build",
        cascade="all, delete-orphan", order_by="ReferenceBuildPart.sort_order",
    )
    pc_build = relationship("PCBuild")
class ReferenceBuildPart(Base):
    __tablename__ = "reference_build_parts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    build_id  = Column(UUID(as_uuid=True), ForeignKey("reference_builds.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    part_id = Column(UUID(as_uuid=True), ForeignKey("pc_parts.id", ondelete="RESTRICT"),
                          nullable=False, index=True)
    component = Column(String(50), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    approx_price = Column(Integer, nullable=False)
    approx_price_updated_at = Column(DateTime(timezone=True), nullable=True,
    comment="When approx_price was last manually reviewed or updated")

    build = relationship("ReferenceBuild", back_populates="parts")
    part  = relationship("PCPart")