"""Loading, routing and serializing orders for the staff screens (waiter, kitchen, bar, floor)."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.core.activity import log_activity
from app.core.config import settings
from app.core.push import notify_order_for_waiter
from app.core.stations import CLOSED_STATUSES, Station, display_status, station_for_item
from app.models.menu import MenuItem
from app.models.order import Order, OrderItem
from app.models.table import RestaurantTable
from app.models.user import ShiftStatus, StaffRole, User

STAFF_ORDER_OPTIONS = (
    joinedload(Order.table),
    joinedload(Order.customer),
    joinedload(Order.payment),
    joinedload(Order.waiter),
    joinedload(Order.items).joinedload(OrderItem.menu_item).joinedload(MenuItem.category),
)


def open_orders(db: Session):
    """Orders still in progress, oldest first (the order staff should work through them)."""
    return (
        db.query(Order)
        .options(*STAFF_ORDER_OPTIONS)
        .filter(Order.status.notin_(CLOSED_STATUSES))
        .order_by(Order.created_at.asc(), Order.id.asc())
    )


def get_staff_order(db: Session, order_id: int) -> Optional[Order]:
    return db.query(Order).options(*STAFF_ORDER_OPTIONS).filter(Order.id == order_id).first()


def is_available_waiter(user: Optional[User]) -> bool:
    return bool(
        user
        and user.active
        and user.role == StaffRole.waiter.value
        and user.shift == ShiftStatus.on_shift.value
    )


def route_waiter(table: RestaurantTable) -> Optional[User]:
    """The waiter a new order from this table goes to: the table's waiter if they're
    on shift; otherwise nobody, and the order waits in the admin's unassigned queue."""
    return table.waiter if is_available_waiter(table.waiter) else None


def start_shift(db: Session, user: User, new_session: bool = False) -> list[int]:
    """Put someone on shift until their PIN session expires (a new sign-in restarts the clock;
    coming back from a break doesn't). A waiter also picks up orders that came from their
    tables while they were away and are still unassigned, so nothing waits on an admin who
    may not be watching. Returns the orders picked up."""
    now = datetime.now(timezone.utc)
    if new_session or utc(user.shift_ends_at) is None or utc(user.shift_ends_at) <= now:
        user.shift_ends_at = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    user.shift_status = ShiftStatus.on_shift.value
    table_ids = [table.id for table in user.tables]
    if user.role != StaffRole.waiter.value or not table_ids:
        return []
    waiting = open_orders(db).filter(Order.waiter_id.is_(None), Order.table_id.in_(table_ids)).all()
    for order in waiting:
        log_activity(
            db, "order.assigned", actor=user, order_id=order.id, table_id=order.table_id,
            reason="returned", from_staff=None, to_staff=user.name,
        )
        order.waiter = user
        order.assigned_at = now
        order.accepted_at = None
        notify_order_for_waiter(db, order, user.id, moved=True)
    return [order.id for order in waiting]


def utc(moment: Optional[datetime]) -> Optional[datetime]:
    """Timestamps are stored in UTC; SQLite hands them back without a zone, so add it
    (screens compute "waiting 4 min" from these)."""
    return moment.replace(tzinfo=timezone.utc) if moment and moment.tzinfo is None else moment


def serialize_staff_order(order: Order, station: Optional[Station] = None) -> dict:
    """An order as staff see it. With station set, only that station's lines are included."""
    lines = []
    for item in order.items:
        line_station = station_for_item(item)
        if station and line_station != station:
            continue
        lines.append(
            {
                "name": item.menu_item.name if item.menu_item else f"Removed item #{item.menu_item_id}",
                "quantity": item.quantity,
                "station": line_station.value,
            }
        )
    payment = order.payment
    return {
        "id": order.id,
        "table_number": order.table_number,
        "customer_name": order.customer.name if order.customer else "Guest",
        "customer_phone": order.customer.phone_number if order.customer else "",
        "notes": order.notes,
        "status": display_status(order),
        "total": order.total_amount,
        "payment_status": payment.status.value if payment else "unpaid",
        "payment_provider": payment.provider if payment else None,
        "payment_method": payment.method if payment else None,
        "waiter": {"id": order.waiter.id, "display_name": order.waiter.name} if order.waiter else None,
        "assigned_at": utc(order.assigned_at),
        "accepted_at": utc(order.accepted_at),
        "kitchen_status": order.kitchen_status,
        "bar_status": order.bar_status,
        "ready_at": utc(order.ready_at),
        "served_at": utc(order.served_at),
        "created_at": utc(order.created_at),
        "items": lines,
    }
