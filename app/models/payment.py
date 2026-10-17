from sqlalchemy import Column, String, Float, ForeignKey, Integer, Enum as SQLEnum
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
    provider = Column(String, nullable=True) # "demo" today; paystack/hubtel once integrated
    reference = Column(String, nullable=True, unique=True)
    method = Column(String(16), nullable=True)  # "momo" or "card"
    network = Column(String(16), nullable=True)  # MoMo network: "mtn", "telecel", "at"

    order = relationship("Order", back_populates="payment")
