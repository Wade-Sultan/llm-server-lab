import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Base schemas
# ---------------------------------------------------------------------------

class ListingBase(BaseModel):
    part_id: uuid.UUID
    listing_type: str = Field(..., description="e.g. 'amazon', 'ebay'")
    marketplace: str = Field(..., description="Marketplace name, e.g. 'amazon', 'ebay'")
    url: Optional[str] = None
    price_amount: Optional[int] = Field(None, description="Price in smallest currency denomination (cents)")
    currency: Optional[str] = "USD"
    image_url: Optional[str] = None
    is_active: bool = True


class ListingCreate(ListingBase):
    """Schema for creating a new listing."""
    pass


class ListingUpdate(BaseModel):
    """Schema for updating a listing. All fields optional."""
    url: Optional[str] = None
    price_amount: Optional[int] = None
    currency: Optional[str] = None
    image_url: Optional[str] = None
    is_active: Optional[bool] = None
    fetched_at: Optional[datetime] = None
    price_last_updated_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Public/read schemas
# ---------------------------------------------------------------------------

class ListingPublic(BaseModel):
    """Schema for returning listing data to clients."""

    id: uuid.UUID
    part_id: uuid.UUID
    listing_type: str
    marketplace: str
    url: Optional[str] = None
    price_amount: Optional[int] = None
    currency: Optional[str] = None
    image_url: Optional[str] = None
    is_active: bool
    fetched_at: Optional[datetime] = None
    price_last_updated_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ListingsPublic(BaseModel):
    """Container for multiple listings."""

    data: list[ListingPublic]
    count: int


# ---------------------------------------------------------------------------
# Amazon-specific schemas
# ---------------------------------------------------------------------------

class AmazonListingBase(ListingBase):
    asin: str = Field(..., max_length=32)
    is_prime: Optional[bool] = None
    seller_name: Optional[str] = None
    affiliate_tag: Optional[str] = None


class AmazonListingCreate(AmazonListingBase):
    """Schema for creating an Amazon listing."""
    pass


class AmazonListingPublic(ListingPublic):
    """Schema for returning Amazon listing data."""

    asin: str
    is_prime: Optional[bool] = None
    seller_name: Optional[str] = None
    affiliate_tag: Optional[str] = None


# ---------------------------------------------------------------------------
# eBay-specific schemas
# ---------------------------------------------------------------------------

class EbayListingBase(ListingBase):
    ebay_item_id: str = Field(..., max_length=64)
    condition: Optional[str] = None
    seller_feedback_score: Optional[int] = None
    buy_it_now: Optional[bool] = None


class EbayListingCreate(EbayListingBase):
    """Schema for creating an eBay listing."""
    pass


class EbayListingPublic(ListingPublic):
    """Schema for returning eBay listing data."""

    ebay_item_id: str
    condition: Optional[str] = None
    seller_feedback_score: Optional[int] = None
    buy_it_now: Optional[bool] = None
