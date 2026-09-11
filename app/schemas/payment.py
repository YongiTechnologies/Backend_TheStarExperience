from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime
from app.models.payment import PaymentStatus

class PaymentInitializeRequest(BaseModel):
    order_id: int
    provider: Optional[str] = "paystack"

class PaymentInitializeResponse(BaseModel):
    payment_id: int
    order_id: int
    amount: float
    status: PaymentStatus
    provider: Optional[str] = None
    reference: str
    checkout_url: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class PaymentResponse(BaseModel):
    id: int
    order_id: int
    amount: float
    status: PaymentStatus
    provider: Optional[str] = None
    reference: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
