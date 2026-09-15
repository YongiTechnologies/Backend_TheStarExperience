from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.core.security import require_admin
from app.database.connection import get_db
from app.models.menu import MenuCategory, MenuItem
from app.models.order import Order, OrderItem, OrderStatus
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
                "name": item.menu_item.name,
                "price": item.unit_price,
                "quantity": item.quantity,
                "category": item.menu_item.category.name if item.menu_item.category else None,
                "notes": None,
            }
            for item in order.items
        ],
        "subtotal": order.total_amount,
        "total": order.total_amount,
        "status": "received" if order.status == OrderStatus.pending else order.status.value,
        "payment_status": (
            "pending"
            if not order.payment or order.payment.status.value in {"unpaid", "pending"}
            else order.payment.status.value
        ),
        "payment_method": order.payment.provider if order.payment else None,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
        "notes": None,
    }


def get_order_or_404(order_id: int, db: Session) -> Order:
    order = (
        db.query(Order)
        .options(
            joinedload(Order.customer),
            joinedload(Order.table),
            joinedload(Order.payment),
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
    db: Session = Depends(get_db),
):
    order = get_order_or_404(order_id, db)
    database_status = (
        OrderStatus.pending
        if status_in.status == "received"
        else OrderStatus.preparing
        if status_in.status == "accepted"
        else OrderStatus(status_in.status)
    )
    order.status = database_status
    db.commit()
    return serialize_order(get_order_or_404(order_id, db))
