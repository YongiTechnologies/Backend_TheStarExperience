from sqlalchemy import Column, String, Float, ForeignKey, Integer, Enum as SQLEnum
from app.models.base import BaseModel
from sqlalchemy.orm import relationship
import enum

class OrderStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    preparing = "preparing"
    ready = "ready"
    completed = "completed"
    cancelled = "cancelled"

class Order(BaseModel):
    __tablename__ = "orders"
    
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    table_id = Column(Integer, ForeignKey("restaurant_tables.id"), nullable=False)
    status = Column(SQLEnum(OrderStatus), default=OrderStatus.pending, nullable=False)
    total_amount = Column(Float, default=0.0, nullable=False)
    
    customer = relationship("Customer", back_populates="orders")
    table = relationship("RestaurantTable", back_populates="orders")
    items = relationship("OrderItem", back_populates="order")
    payment = relationship("Payment", back_populates="order", uselist=False)

class OrderItem(BaseModel):
    __tablename__ = "order_items"
    
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    menu_item_id = Column(Integer, ForeignKey("menu_items.id"), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Float, nullable=False)
    
    order = relationship("Order", back_populates="items")
    menu_item = relationship("MenuItem")
