from sqlalchemy import Column, String
from app.models.base import BaseModel
from sqlalchemy.orm import relationship

class Customer(BaseModel):
    __tablename__ = "customers"
    
    name = Column(String, nullable=False)
    phone_number = Column(String, nullable=False)
    
    orders = relationship("Order", back_populates="customer")
