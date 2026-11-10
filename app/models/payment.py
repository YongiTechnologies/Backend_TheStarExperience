from sqlalchemy import Column, String, Float, ForeignKey, Integer, Text, Enum as SQLEnum
from app.models.base import BaseModel
from sqlalchemy.orm import relationship
import enum

class PaymentStatus(str, enum.Enum):
    unpaid = "unpaid"
    pending = "pending"
    paid = "paid"
    failed = "failed"
    refunded = "refunded"

class Payment(BaseModel):
    __tablename__ = "payments"

    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, unique=True)
    amount = Column(Float, nullable=False)
    status = Column(SQLEnum(PaymentStatus), default=PaymentStatus.unpaid, nullable=False)
    provider = Column(String, nullable=True) # "demo" or "paystack"
    reference = Column(String, nullable=True, unique=True)
    method = Column(String(16), nullable=True)  # "momo" or "card"
    network = Column(String(16), nullable=True)  # MoMo network: "mtn", "telecel", "at"
    # Loyverse receipt for this sale once synced (see app/core/loyverse.py).
    loyverse_receipt_number = Column(String(32), nullable=True)
    loyverse_sync_status = Column(String(16), nullable=True)  # "synced" | "failed"; NULL until paid
    loyverse_sync_error = Column(Text, nullable=True)

    order = relationship("Order", back_populates="payment")
