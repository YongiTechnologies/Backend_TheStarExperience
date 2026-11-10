from sqlalchemy import Column, Float, String, Text

from app.models.base import BaseModel


class SimulatedLoyverseReceipt(BaseModel):
    """Stands in for Loyverse's receipt list while LOYVERSE_MODE is "simulated" (see app/core/loyverse.py)."""
    __tablename__ = "loyverse_simulated_receipts"

    # Loyverse-style "<pos device>-<number>", assigned once the row has an id.
    receipt_number = Column(String(32), nullable=True, unique=True, index=True)
    order_ref = Column(String(64), nullable=False, index=True)
    total_money = Column(Float, nullable=False)
    payment_type = Column(String(40), nullable=False)
    payload = Column(Text, nullable=False)  # JSON body the real POST /v1.0/receipts would get
