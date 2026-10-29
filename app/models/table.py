from sqlalchemy import Column, ForeignKey, String, Integer
from app.models.base import BaseModel
from sqlalchemy.orm import relationship

class RestaurantTable(BaseModel):
    __tablename__ = "restaurant_tables"

    table_number = Column(String, unique=True, nullable=False, index=True)
    capacity = Column(Integer, nullable=True)
    # The waiter looking after this table for the shift; new orders route to them.
    waiter_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    orders = relationship("Order", back_populates="table")
    waiter = relationship("User", back_populates="tables")
