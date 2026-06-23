"""
API routes for managing PC part listings.

Each listing belongs to exactly one PCPart, but a PCPart can have multiple
listings across different marketplaces (Amazon, eBay, etc.).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from app.api.deps import SessionDep, get_current_active_superuser
from app.crud import listings as listings_crud
from app.models.listing import Listing
from app.models.pcparts import PCPart
from app.schemas.listing import (
    ListingCreate,
    ListingPublic,
    ListingUpdate,
    ListingsPublic,
)

router = APIRouter(prefix="/listings", tags=["listings"])


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------

@router.get("/", response_model=ListingsPublic)
async def read_listings(
    session: SessionDep,
    part_id: uuid.UUID | None = None,
    marketplace: str | None = None,
    skip: int = 0,
    limit: int = 100,
) -> Any:
    """
    Retrieve listings with optional filtering by part_id and/or marketplace.
    """
    listings = await listings_crud.get_listings_with_parts(
        session=session,
        part_id=part_id,
        marketplace=marketplace,
        active_only=True,
        skip=skip,
        limit=limit,
    )

    # Count total matching listings
    count_stmt = select(func.count(Listing.id)).where(Listing.is_active == True)  # noqa: E712
    if part_id is not None:
        count_stmt = count_stmt.where(Listing.part_id == part_id)
    if marketplace is not None:
        count_stmt = count_stmt.where(Listing.marketplace == marketplace)
    count_result = await session.execute(count_stmt)
    count = count_result.scalar() or 0

    return ListingsPublic(data=listings, count=count)


@router.get("/{listing_id}", response_model=ListingPublic)
async def read_listing(
    listing_id: uuid.UUID,
    session: SessionDep,
) -> Any:
    """
    Retrieve a single listing by ID.
    """
    db_listing = await listings_crud.get_listing(session=session, listing_id=listing_id)
    if db_listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")
    return db_listing


@router.get("/by-part/{part_id}", response_model=ListingsPublic)
async def read_listings_by_part(
    part_id: uuid.UUID,
    session: SessionDep,
) -> Any:
    """
    Retrieve all active listings for a specific PCPart.

    A single PCPart can have multiple listings across different marketplaces.
    """
    # Verify the part exists
    part = await session.get(PCPart, part_id)
    if part is None:
        raise HTTPException(status_code=404, detail="PCPart not found")

    listings = await listings_crud.get_listings_by_part(
        session=session,
        part_id=part_id,
        active_only=True,
    )
    return ListingsPublic(data=listings, count=len(listings))


# ---------------------------------------------------------------------------
# Create operation (superuser only)
# ---------------------------------------------------------------------------

@router.post(
    "/",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=ListingPublic,
)
async def create_listing(
    *,
    session: SessionDep,
    listing_in: ListingCreate,
) -> Any:
    """
    Create a new listing for a PCPart. Requires superuser privileges.
    """
    try:
        db_listing = await listings_crud.create_listing(
            session=session,
            listing_in=listing_in,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return db_listing


# ---------------------------------------------------------------------------
# Update operation (superuser only)
# ---------------------------------------------------------------------------

@router.patch(
    "/{listing_id}",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=ListingPublic,
)
async def update_listing(
    *,
    session: SessionDep,
    listing_id: uuid.UUID,
    listing_in: ListingUpdate,
) -> Any:
    """
    Update a listing. Requires superuser privileges.
    """
    db_listing = await listings_crud.get_listing(session=session, listing_id=listing_id)
    if db_listing is None:
        raise HTTPException(status_code=404, detail="Listing not found")

    db_listing = await listings_crud.update_listing(
        session=session,
        db_listing=db_listing,
        listing_in=listing_in,
    )
    return db_listing


# ---------------------------------------------------------------------------
# Delete operation (superuser only)
# ---------------------------------------------------------------------------

@router.delete(
    "/{listing_id}",
    dependencies=[Depends(get_current_active_superuser)],
)
async def delete_listing(
    listing_id: uuid.UUID,
    session: SessionDep,
) -> dict[str, str]:
    """
    Delete a listing by ID. Requires superuser privileges.
    """
    deleted = await listings_crud.remove_listing(session=session, listing_id=listing_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Listing not found")
    return {"message": "Listing deleted successfully"}
