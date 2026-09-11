from sqlalchemy import Column, String, Integer
from app.models.base import BaseModel
from sqlalchemy.orm import relationship

class RestaurantTable(BaseModel):
    __tablename__ = "restaurant_tables"
    
    table_number = Column(String, unique=True, nullable=False, index=True)
    capacity = Column(Integer, nullable=True)
    
    orders = relationship("Order", back_populates="table")
