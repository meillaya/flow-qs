from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NonBlankString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PositiveStrictInt = Annotated[int, Field(strict=True, gt=0)]


class OrderItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sku: NonBlankString
    qty: PositiveStrictInt


class OrderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: NonBlankString
    items: Annotated[list[OrderItem], Field(min_length=1)]
    total_cents: PositiveStrictInt
    payment_charge_id: NonBlankString | None = None
    payment_order_id: NonBlankString | None = None

    @model_validator(mode="after")
    def payment_identity_is_complete(self) -> "OrderCreateRequest":
        if (self.payment_charge_id is None) != (self.payment_order_id is None):
            raise ValueError("payment_charge_id and payment_order_id must be provided together")
        return self
