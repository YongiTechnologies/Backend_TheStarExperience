from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
from typing import List, Literal, Optional
from datetime import datetime
from app.models.order import OrderStatus
from app.schemas.customer import CustomerResponse
from app.schemas.menu import MenuItemResponse

class OrderItemBase(BaseModel):
    menu_item_id: int = Field(validation_alias=AliasChoices("menu_item_id", "menuItemId", "id"))
    quantity: int = Field(default=1, ge=1)

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
    customer_name: str = Field(validation_alias=AliasChoices("customer_name", "customerName", "name"))
    customer_phone: str = Field(validation_alias=AliasChoices("customer_phone", "customerPhone", "phone"))
    items: List[OrderItemCreate]

    @model_validator(mode="after")
    def require_table_reference(self):
        if self.table_id is None and not self.table_number:
            raise ValueError("Either table_id or table_number is required")
        return self

class OrderResponse(BaseModel):
    id: int
    customer_id: int
    table_id: int
    status: OrderStatus
    total_amount: float
    customer: Optional[CustomerResponse] = None
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
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    notes: Optional[str] = None


class AdminOrderStatusUpdate(BaseModel):
    status: Literal["received", "accepted", "preparing", "ready", "completed"]
