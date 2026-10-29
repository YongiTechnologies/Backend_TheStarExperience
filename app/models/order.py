from sqlalchemy import Column, DateTime, String, Float, ForeignKey, Integer, Text, Enum as SQLEnum
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
    # Guest's note for the bar/kitchen, e.g. "no ice, extra shito".
    notes = Column(Text, nullable=True)
    # Secret handed to the guest at checkout; required to view the order publicly.
    # Nullable so orders placed before it existed stay valid (see schema_upgrades.py).
    tracking_token = Column(String(64), nullable=True, unique=True, index=True)
    # Client-generated key so a retried "place order" request returns the
    # original order instead of creating a duplicate.
    idempotency_key = Column(String(64), nullable=True, unique=True, index=True)
    # Service: the waiter responsible (routed from the table) and when they accepted.
    waiter_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    # When the current waiter got the order (routing or reassignment): the floor's
    # "not accepted yet" alert counts from here, not from when the guest ordered.
    assigned_at = Column(DateTime(timezone=True), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    # Per-station progress ("queued" | "preparing" | "ready"); NULL when the
    # order has nothing for that station. See app/core/stations.py.
    kitchen_status = Column(String(16), nullable=True)
    bar_status = Column(String(16), nullable=True)
    ready_at = Column(DateTime(timezone=True), nullable=True)
    served_at = Column(DateTime(timezone=True), nullable=True)

    customer = relationship("Customer", back_populates="orders")
    table = relationship("RestaurantTable", back_populates="orders")
    items = relationship("OrderItem", back_populates="order")
    payment = relationship("Payment", back_populates="order", uselist=False)
    waiter = relationship("User")

    @property
    def table_number(self):
        """The number printed on the table (what guests and staff see), not its database id."""
        return self.table.table_number if self.table else None

    @property
    def waiter_name(self):
        """Shown to the guest once their waiter has accepted the order."""
        return self.waiter.name if self.waiter and self.accepted_at else None

class OrderItem(BaseModel):
    __tablename__ = "order_items"
    
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    menu_item_id = Column(Integer, ForeignKey("menu_items.id"), nullable=False)
    quantity = Column(Integer, nullable=False, default=1)
    unit_price = Column(Float, nullable=False)
    # Kitchen or bar, fixed when ordered so recategorising a menu item mid-service
    # can't move a line away from the station already working on it.
    station = Column(String(16), nullable=True)

    order = relationship("Order", back_populates="items")
    menu_item = relationship("MenuItem")
