"""
CRUD operations for PC part listings.

One PCPart can have multiple listings (e.g., the same GPU sold on Amazon and eBay).
Each listing belongs to exactly one PCPart via the part_id foreign key.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session, contains_eager

from app.models.listing import (
    AmazonListing,
    EbayListing,
    Listing,
)
from app.models.pcparts import PCPart
from app.schemas.listing import (
    ListingCreate,
    ListingUpdate,
)


# ---------------------------------------------------------------------------
# Generic listing operations
# ---------------------------------------------------------------------------

def create_listing(
    *,
    session: Session,
    listing_in: ListingCreate,
) -> Listing:
    """
    Create a new listing for a PCPart.

    Validates that the referenced PCPart exists before inserting.
    """
    # Ensure the part exists
    part = session.get(PCPart, listing_in.part_id)
    if part is None:
        raise ValueError(f"PCPart with id {listing_in.part_id} not found")

    db_obj = Listing(
        part_id=listing_in.part_id,
        listing_type=listing_in.listing_type,
        marketplace=listing_in.marketplace,
        url=listing_in.url,
        price_amount=listing_in.price_amount,
        currency=listing_in.currency,
        image_url=listing_in.image_url,
        is_active=listing_in.is_active,
    )
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj


def get_listing(
    *,
    session: Session,
    listing_id: uuid.UUID,
) -> Listing | None:
    """Retrieve a listing by its primary key."""
    return session.get(Listing, listing_id)


def get_listings_by_part(
    *,
    session: Session,
    part_id: uuid.UUID,
    active_only: bool = True,
) -> list[Listing]:
    """
    Retrieve all listings for a given PCPart.

    One PCPart can have many listings (e.g., Amazon, eBay, Newegg).
    """
    stmt = select(Listing).where(Listing.part_id == part_id)
    if active_only:
        stmt = stmt.where(Listing.is_active == True)  # noqa: E712
    return list(session.scalars(stmt).all())


def get_listing_with_part(
    *,
    session: Session,
    listing_id: uuid.UUID,
) -> Listing | None:
    """
    Retrieve a listing along with its related PCPart in a single query.

    This eager-loads the part relationship so the caller can access
    listing.part without triggering additional queries.
    """
    stmt = (
        select(Listing)
        .options(contains_eager(Listing.part))
        .join(PCPart)
        .where(Listing.id == listing_id)
    )
    return session.scalars(stmt).first()


def get_listings_with_parts(
    *,
    session: Session,
    part_id: uuid.UUID | None = None,
    marketplace: str | None = None,
    active_only: bool = True,
    skip: int = 0,
    limit: int = 100,
) -> list[Listing]:
    """
    Retrieve listings with their related PCParts eager-loaded.

    Supports optional filtering by part_id and/or marketplace.
    """
    stmt = (
        select(Listing)
        .options(contains_eager(Listing.part))
        .join(PCPart)
    )
    if part_id is not None:
        stmt = stmt.where(Listing.part_id == part_id)
    if marketplace is not None:
        stmt = stmt.where(Listing.marketplace == marketplace)
    if active_only:
        stmt = stmt.where(Listing.is_active == True)  # noqa: E712
    stmt = stmt.offset(skip).limit(limit)
    return list(session.scalars(stmt).all())


def update_listing(
    *,
    session: Session,
    db_listing: Listing,
    listing_in: ListingUpdate,
) -> Listing:
    """Update a listing with the provided fields (partial update)."""
    update_data = listing_in.model_dump(exclude_unset=True)

    for field, value in update_data.items():
        setattr(db_listing, field, value)

    session.add(db_listing)
    session.commit()
    session.refresh(db_listing)
    return db_listing


def remove_listing(
    *,
    session: Session,
    listing_id: uuid.UUID,
) -> bool:
    """
    Delete a listing by ID.

    Returns True if the listing was found and deleted, False otherwise.
    """
    db_listing = session.get(Listing, listing_id)
    if db_listing is None:
        return False

    session.delete(db_listing)
    session.commit()
    return True


def count_listings_by_part(
    *,
    session: Session,
    part_id: uuid.UUID,
    active_only: bool = True,
) -> int:
    """Count the number of listings for a given PCPart."""
    stmt = select(func.count(Listing.id)).where(Listing.part_id == part_id)
    if active_only:
        stmt = stmt.where(Listing.is_active == True)  # noqa: E712
    return session.scalar(stmt) or 0


# ---------------------------------------------------------------------------
# Amazon-specific operations
# ---------------------------------------------------------------------------

def get_amazon_listing(
    *,
    session: Session,
    asin: str,
) -> AmazonListing | None:
    """Retrieve an Amazon listing by its ASIN."""
    stmt = select(AmazonListing).where(AmazonListing.asin == asin)
    return session.scalars(stmt).first()


def get_amazon_listings_by_part(
    *,
    session: Session,
    part_id: uuid.UUID,
    active_only: bool = True,
) -> list[AmazonListing]:
    """Retrieve all Amazon listings for a given PCPart."""
    stmt = (
        select(AmazonListing)
        .join(Listing)
        .where(Listing.part_id == part_id)
    )
    if active_only:
        stmt = stmt.where(Listing.is_active == True)  # noqa: E712
    return list(session.scalars(stmt).all())


# ---------------------------------------------------------------------------
# eBay-specific operations
# ---------------------------------------------------------------------------

def get_ebay_listing(
    *,
    session: Session,
    ebay_item_id: str,
) -> EbayListing | None:
    """Retrieve an eBay listing by its item ID."""
    stmt = select(EbayListing).where(EbayListing.ebay_item_id == ebay_item_id)
    return session.scalars(stmt).first()


def get_ebay_listings_by_part(
    *,
    session: Session,
    part_id: uuid.UUID,
    active_only: bool = True,
) -> list[EbayListing]:
    """Retrieve all eBay listings for a given PCPart."""
    stmt = (
        select(EbayListing)
        .join(Listing)
        .where(Listing.part_id == part_id)
    )
    if active_only:
        stmt = stmt.where(Listing.is_active == True)  # noqa: E712
    return list(session.scalars(stmt).all())
