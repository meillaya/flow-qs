"""Pydantic models for catalog products."""

from pydantic import BaseModel, Field


class Product(BaseModel):
    """A catalog product."""

    id: str = Field(..., description="Unique product identifier")
    name: str = Field(..., min_length=1, description="Product name")
    description: str = Field(default="", description="Product description")
    price_cents: int = Field(..., ge=0, description="Price in cents")
    sku: str = Field(..., min_length=1, description="Stock keeping unit")


class ProductCreate(BaseModel):
    """Payload for creating a new product."""

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    description: str = Field(default="")
    price_cents: int = Field(..., ge=0)
    sku: str = Field(..., min_length=1)
