from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.activity import log_activity
from app.core.events import emit
from app.core.push import notify_ready
from app.core.security import require_admin
from app.core.staff_orders import utc
from app.core.stations import Station, StationStatus, display_status, get_station_status, set_station_status
from app.database.connection import get_db
from app.models.menu import MenuCategory, MenuItem
from app.models.order import Order, OrderItem, OrderStatus
from app.models.user import User
from app.schemas.menu import (
    MenuCategoryCreate,
    MenuCategoryResponse,
    MenuItemAvailabilityUpdate,
    MenuItemCreate,
    MenuItemResponse,
    MenuItemUpdate,
)
from app.schemas.order import AdminOrderResponse, AdminOrderStatusUpdate

router = APIRouter(dependencies=[Depends(require_admin)])


def serialize_order(order: Order) -> dict:
    """Convert database relationships into the frontend admin dashboard contract."""
    return {
        "id": order.id,
        "table_number": order.table.table_number,
        "customer_name": order.customer.name,
        "customer_phone": order.customer.phone_number,
        "items": [
            {
                "id": str(item.id),
                # Tolerate lines whose menu item was removed, so one bad row can't take down the board.
                "name": item.menu_item.name if item.menu_item else f"Removed item #{item.menu_item_id}",
                "price": item.unit_price,
                "quantity": item.quantity,
                "category": item.menu_item.category.name if item.menu_item and item.menu_item.category else None,
                "notes": None,
            }
            for item in order.items
        ],
        "subtotal": order.total_amount,
        "total": order.total_amount,
        "status": display_status(order),
        "payment_status": (
            "pending"
            if not order.payment or order.payment.status.value in {"unpaid", "pending"}
            else order.payment.status.value
        ),
        "payment_method": order.payment.method if order.payment else None,
        "momo_network": order.payment.network if order.payment else None,
        "payment_provider": order.payment.provider if order.payment else None,
        "created_at": utc(order.created_at),
        "updated_at": utc(order.updated_at),
        "notes": order.notes,
        "waiter_id": order.waiter_id,
        "waiter_name": order.waiter.name if order.waiter else None,
        "accepted_at": utc(order.accepted_at),
        "kitchen_status": order.kitchen_status,
        "bar_status": order.bar_status,
        "ready_at": utc(order.ready_at),
        "served_at": utc(order.served_at),
    }


def get_order_or_404(order_id: int, db: Session) -> Order:
    order = (
        db.query(Order)
        .options(
            joinedload(Order.customer),
            joinedload(Order.table),
            joinedload(Order.payment),
            joinedload(Order.waiter),
            joinedload(Order.items).joinedload(OrderItem.menu_item).joinedload(MenuItem.category),
        )
        .filter(Order.id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


@router.post("/menu/categories", response_model=MenuCategoryResponse, status_code=status.HTTP_201_CREATED)
def create_menu_category(category_in: MenuCategoryCreate, db: Session = Depends(get_db)):
    existing = db.query(MenuCategory).filter(MenuCategory.name == category_in.name).first()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A category with this name already exists")

    category = MenuCategory(**category_in.model_dump())
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.post("/menu/items", response_model=MenuItemResponse, status_code=status.HTTP_201_CREATED)
def create_menu_item(item_in: MenuItemCreate, db: Session = Depends(get_db)):
    category = db.get(MenuCategory, item_in.category_id)
    if not category:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Menu category not found")

    item = MenuItem(**item_in.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.get("/menu/items", response_model=List[MenuItemResponse])
def list_menu_items(db: Session = Depends(get_db)):
    return db.query(MenuItem).order_by(MenuItem.id).all()


@router.patch("/menu/items/{item_id}", response_model=MenuItemResponse)
def update_menu_item(item_id: int, item_in: MenuItemUpdate, db: Session = Depends(get_db)):
    item = db.get(MenuItem, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Menu item not found")

    changes = item_in.model_dump(exclude_unset=True)
    if "category_id" in changes and not db.get(MenuCategory, changes["category_id"]):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Menu category not found")
    for field, value in changes.items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/menu/items/{item_id}/availability", response_model=MenuItemResponse)
def update_menu_item_availability(
    item_id: int,
    availability_in: MenuItemAvailabilityUpdate,
    db: Session = Depends(get_db),
):
    item = db.get(MenuItem, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Menu item not found")
    item.is_available = availability_in.is_available
    db.commit()
    db.refresh(item)
    return item


@router.delete("/menu/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_menu_item(item_id: int, db: Session = Depends(get_db)) -> Response:
    item = db.get(MenuItem, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Menu item not found")
    # Checked explicitly: SQLite doesn't enforce foreign keys unless asked, and a
    # deleted item would orphan its order lines and break the order board.
    if db.query(OrderItem.id).filter(OrderItem.menu_item_id == item_id).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This menu item cannot be deleted because it belongs to an order",
        )
    db.delete(item)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This menu item cannot be deleted because it belongs to an order",
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/menu/categories", response_model=List[MenuCategoryResponse])
def list_menu_categories(db: Session = Depends(get_db)):
    return db.query(MenuCategory).order_by(MenuCategory.id).all()


@router.get("/orders", response_model=List[AdminOrderResponse])
def get_all_orders(db: Session = Depends(get_db)):
    """Return every placed order, including its current status and line items."""
    orders = (
        db.query(Order)
        .options(
            joinedload(Order.customer),
            joinedload(Order.table),
            joinedload(Order.payment),
            joinedload(Order.waiter),
            joinedload(Order.items).joinedload(OrderItem.menu_item).joinedload(MenuItem.category),
        )
        .order_by(Order.created_at.desc())
        .all()
    )
    return [serialize_order(order) for order in orders]


@router.patch("/orders/{order_id}/status", response_model=AdminOrderResponse)
def update_order_status(
    order_id: int,
    status_in: AdminOrderStatusUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin override of an order's stage (stations and waiter normally drive it)."""
    order = get_order_or_404(order_id, db)
    database_status = (
        OrderStatus.pending
        if status_in.status == "received"
        else OrderStatus.preparing
        if status_in.status == "accepted"
        else OrderStatus(status_in.status)
    )
    now = datetime.now(timezone.utc)
    was_ready = order.status == OrderStatus.ready
    order.status = database_status
    # Keep the kitchen and bar consistent with the override, so their screens and the
    # waiter's "Mark served" agree with what the admin chose.
    present = [station for station in Station if get_station_status(order, station) is not None]
    if database_status in (OrderStatus.ready, OrderStatus.completed):
        for station in present:
            set_station_status(order, station, StationStatus.ready.value)
        order.ready_at = order.ready_at or now
    else:
        # Moving an order back reopens it: it is no longer ready or served.
        order.ready_at = None
        order.served_at = None
        if database_status == OrderStatus.pending:
            for station in present:
                set_station_status(order, station, StationStatus.queued.value)
        elif all(get_station_status(order, station) == StationStatus.ready.value for station in present):
            for station in present:
                set_station_status(order, station, StationStatus.preparing.value)
    if database_status == OrderStatus.completed:
        order.served_at = order.served_at or now
    if database_status == OrderStatus.ready and not was_ready:
        notify_ready(db, order)
    log_activity(db, "order.status_override", actor=admin, order_id=order.id, table_id=order.table_id, status=status_in.status)
    emit(db, "order.updated", order_id=order.id, table_number=order.table_number)
    db.commit()
    return serialize_order(get_order_or_404(order_id, db))
