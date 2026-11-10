from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal, Optional
from datetime import datetime
from app.models.payment import PaymentStatus

PaymentMethod = Literal["momo", "card"]
MomoNetwork = Literal["mtn", "telecel", "at"]


class OrderAccess(BaseModel):
    """Proof the caller placed the order: the tracking token returned at checkout."""
    order_id: int
    tracking_token: str = Field(min_length=1, max_length=64)


class PaymentInitializeRequest(OrderAccess):
    method: PaymentMethod = "momo"
    network: Optional[MomoNetwork] = None
    # Ignored: the server's PAYMENT_PROVIDER decides, so a guest can't pick "demo" to skip paying.
    provider: Optional[str] = None
    # Where Paystack's hosted page returns the guest; used only if it's on an allowed frontend origin.
    callback_url: Optional[str] = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def network_only_for_momo(self):
        if self.method != "momo":
            self.network = None
        return self


class PaymentConfigResponse(BaseModel):
    provider: Literal["demo", "paystack"]


class PaymentVerifyRequest(OrderAccess):
    reference: str = Field(min_length=1, max_length=64)


class PaymentInitializeResponse(BaseModel):
    payment_id: int
    order_id: int
    amount: float
    status: PaymentStatus
    provider: Optional[str] = None
    method: Optional[str] = None
    network: Optional[str] = None
    reference: str
    checkout_url: Optional[str] = None
    # Paystack popup (InlineJS resumeTransaction); None for demo payments.
    access_code: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)

class PaymentResponse(BaseModel):
    id: int
    order_id: int
    amount: float
    status: PaymentStatus
    provider: Optional[str] = None
    reference: Optional[str] = None
    method: Optional[str] = None
    network: Optional[str] = None
    loyverse_receipt_number: Optional[str] = None
    loyverse_sync_status: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class OrderPaymentSummary(BaseModel):
    """Payment details shown alongside an order."""
    status: PaymentStatus
    provider: Optional[str] = None
    method: Optional[str] = None
    network: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)
