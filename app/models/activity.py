from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class ActivityEvent(BaseModel):
    """Audit trail: who assigned, accepted, prepared and served what, and when."""

    __tablename__ = "activity_events"

    action = Column(String(40), nullable=False, index=True)
    actor_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=True, index=True)
    table_id = Column(Integer, ForeignKey("restaurant_tables.id"), nullable=True)
    detail = Column(Text, nullable=True)  # JSON

    actor = relationship("User")
