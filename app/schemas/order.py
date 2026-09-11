from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional
from datetime import datetime
from app.models.order import OrderStatus
from app.schemas.customer import CustomerResponse
from app.schemas.menu import MenuItemResponse

class OrderItemBase(BaseModel):
    menu_item_id: int
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
    table_id: int
    customer_name: str
    customer_phone: str
    items: List[OrderItemCreate]

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
