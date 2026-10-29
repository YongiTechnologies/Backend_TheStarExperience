from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from typing import Annotated, List, Literal, Optional
from datetime import datetime
from app.models.order import OrderStatus
from app.schemas.customer import CustomerResponse
from app.schemas.menu import MenuItemResponse
from app.schemas.payment import OrderPaymentSummary

# Generous upper bounds that still stop absurd or abusive orders.
MAX_QUANTITY_PER_LINE = 50
MAX_LINES_PER_ORDER = 50

CustomerName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
CustomerPhone = Annotated[str, StringConstraints(strip_whitespace=True, min_length=7, max_length=20)]

class OrderItemBase(BaseModel):
    menu_item_id: int = Field(validation_alias=AliasChoices("menu_item_id", "menuItemId", "id"))
    quantity: int = Field(default=1, ge=1, le=MAX_QUANTITY_PER_LINE)

class OrderItemCreate(OrderItemBase):
    pass

class OrderItemResponse(OrderItemBase):
    id: int
    order_id: int
    unit_price: float
    menu_item: Optional[MenuItemResponse] = None

    model_config = ConfigDict(from_attributes=True)

class OrderCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    table_id: Optional[int] = Field(default=None, validation_alias=AliasChoices("table_id", "tableId"))
    table_number: Optional[str] = Field(default=None, validation_alias=AliasChoices("table_number", "tableNumber"))
    customer_name: CustomerName = Field(validation_alias=AliasChoices("customer_name", "customerName", "name"))
    customer_phone: CustomerPhone = Field(validation_alias=AliasChoices("customer_phone", "customerPhone", "phone"))
    items: List[OrderItemCreate] = Field(max_length=MAX_LINES_PER_ORDER)
    notes: Optional[str] = Field(default=None, max_length=300)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")

    @field_validator("notes")
    @classmethod
    def blank_notes_are_none(cls, value: Optional[str]) -> Optional[str]:
        value = value.strip() if value else None
        return value or None

    @model_validator(mode="after")
    def require_table_reference(self):
        if self.table_id is None and not self.table_number:
            raise ValueError("Either table_id or table_number is required")
        return self

class OrderResponse(BaseModel):
    id: int
    customer_id: int
    table_id: int
    table_number: Optional[str] = None
    # First name of the waiter looking after the order, once they've accepted it.
    waiter_name: Optional[str] = None
    status: OrderStatus
    total_amount: float
    notes: Optional[str] = None
    tracking_token: Optional[str] = None
    customer: Optional[CustomerResponse] = None
    payment: Optional[OrderPaymentSummary] = None
    items: List[OrderItemResponse] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class AdminOrderItemResponse(BaseModel):
    id: str
    name: str
    price: float
    quantity: int
    category: Optional[str] = None
    notes: Optional[str] = None


class AdminOrderResponse(BaseModel):
    """Flattened order shape consumed by the admin dashboard."""
    id: int
    table_number: str
    customer_name: str
    customer_phone: str
    items: List[AdminOrderItemResponse]
    subtotal: float
    total: float
    status: str
    payment_status: str
    payment_method: Optional[str] = None
    momo_network: Optional[str] = None
    # "demo" while checkout is simulated: no money has actually been collected.
    payment_provider: Optional[str] = None
    waiter_id: Optional[int] = None
    waiter_name: Optional[str] = None
    accepted_at: Optional[datetime] = None
    kitchen_status: Optional[str] = None
    bar_status: Optional[str] = None
    ready_at: Optional[datetime] = None
    served_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    notes: Optional[str] = None


class AdminOrderStatusUpdate(BaseModel):
    status: Literal["received", "accepted", "preparing", "ready", "completed"]
