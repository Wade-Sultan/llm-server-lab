import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class Listing(Base):
    __tablename__ = "listings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    # A listing targets exactly one of the five columns below: a single part,
    # or one of the four groups the catalog already models. A DB CHECK
    # (ck_listings_one_target) enforces the "exactly one" part, so this is not
    # five independent nullable columns however much it looks like it.
    #
    # Group targeting exists for eBay, whose listings are filtered search URLs:
    # one "RTX 3090" search is correct for every partner board of the chipset,
    # and pinning it to each board means re-entering it and missing boards
    # added later.
    part_id = Column(
        UUID(as_uuid=True),
        ForeignKey("pc_parts.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    gpu_chipset_id = Column(
        UUID(as_uuid=True),
        ForeignKey("gpu_chipsets.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    psu_group_id = Column(
        UUID(as_uuid=True),
        ForeignKey("psu_groups.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    ram_group_id = Column(
        UUID(as_uuid=True),
        ForeignKey("ram_groups.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    storage_group_id = Column(
        UUID(as_uuid=True),
        ForeignKey("storage_groups.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    listing_type = Column(String(20), nullable=False)
    marketplace = Column(String(20), nullable=False)

    url = Column(Text, nullable=True)
    price_amount = Column(
        Integer,
        nullable=True,
        comment="Price in smallest currency denomination (cents, pence, etc.)",
    )
    currency = Column(
        String(3), nullable=True, server_default="USD", comment="ISO 4217 currency code"
    )
    image_url = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="true")

    fetched_at = Column(
        DateTime(timezone=True),
        nullable=True,
        comment="When this listing was last fetched from the marketplace API",
    )
    price_last_updated_at = Column(
        DateTime(timezone=True), nullable=True, comment="When price_amount last changed"
    )

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    part = relationship("PCPart", back_populates="listings")

    __mapper_args__ = {
        "polymorphic_on": listing_type,
        "polymorphic_identity": "listing",
        "polymorphic_load": "selectin",
    }


class AmazonListing(Listing):
    __tablename__ = "amazon_listings"

    id = Column(
        UUID(as_uuid=True),
        ForeignKey("listings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    asin = Column(String(32), nullable=False, index=True)
    brand = Column(
        String(100),
        nullable=True,
        comment="Vendor brand for this listing, e.g. ASUS, MSI (for GPU chipset variants)",
    )
    is_prime = Column(Boolean, nullable=True)
    seller_name = Column(String(255), nullable=True)
    affiliate_tag = Column(
        String(100), nullable=True, comment="Tracking ID at time of fetch"
    )

    __mapper_args__ = {"polymorphic_identity": "amazon"}


class EbayListing(Listing):
    __tablename__ = "ebay_listings"

    id = Column(
        UUID(as_uuid=True),
        ForeignKey("listings.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ebay_item_id = Column(String(64), nullable=False, index=True)
    condition = Column(String(20), nullable=True, comment="new, used, refurbished")
    seller_feedback_score = Column(Integer, nullable=True)
    buy_it_now = Column(Boolean, nullable=True)

    __mapper_args__ = {"polymorphic_identity": "ebay"}
