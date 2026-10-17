from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import List, Optional
from app.core.order_access import ORDER_DETAIL_OPTIONS, get_order_for_guest, guest_order_token, new_tracking_token
from app.core.security import require_admin
from app.database.connection import get_db
from app.models.order import Order, OrderItem, OrderStatus
from app.models.table import RestaurantTable
from app.models.customer import Customer
from app.models.menu import MenuItem
from app.schemas.order import OrderCreate, OrderResponse

router = APIRouter()

@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(order_in: OrderCreate, db: Session = Depends(get_db)):
    # 0. A retry of a request that already succeeded gets the original order back.
    if order_in.idempotency_key:
        existing = _order_by_idempotency_key(db, order_in.idempotency_key)
        if existing:
            return existing

    # 1. Validate restaurant table exists
    if order_in.table_id is not None:
        table = db.query(RestaurantTable).filter(RestaurantTable.id == order_in.table_id).first()
    else:
        table = (
            db.query(RestaurantTable)
            .filter(RestaurantTable.table_number == order_in.table_number)
            .first()
        )
    if not table:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Restaurant table not found"
        )
    
    # 2. Find or create customer
    customer = db.query(Customer).filter(Customer.phone_number == order_in.customer_phone).first()
    if not customer:
        customer = Customer(
            name=order_in.customer_name,
            phone_number=order_in.customer_phone
        )
        db.add(customer)
        db.flush()
    else:
        if order_in.customer_name and customer.name != order_in.customer_name:
            customer.name = order_in.customer_name
            db.flush()

    if not order_in.items:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Order must contain at least one item"
        )

    # 3. Fetch menu items & calculate total
    total_amount = 0.0
    items_to_add = []
    
    for item in order_in.items:
        menu_item = db.query(MenuItem).filter(MenuItem.id == item.menu_item_id).first()
        if not menu_item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Menu item with id {item.menu_item_id} not found"
            )
        if not menu_item.is_available:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Menu item '{menu_item.name}' is currently unavailable"
            )
        
        unit_price = menu_item.price
        total_amount += unit_price * item.quantity
        items_to_add.append((item.menu_item_id, item.quantity, unit_price))

    # 4. Create Order
    new_order = Order(
        customer_id=customer.id,
        table_id=table.id,
        status=OrderStatus.pending,
        total_amount=round(total_amount, 2),
        notes=order_in.notes,
        tracking_token=new_tracking_token(),
        idempotency_key=order_in.idempotency_key,
    )
    db.add(new_order)
    db.flush()

    # 5. Create Order Items
    for menu_item_id, quantity, unit_price in items_to_add:
        order_item = OrderItem(
            order_id=new_order.id,
            menu_item_id=menu_item_id,
            quantity=quantity,
            unit_price=unit_price
        )
        db.add(order_item)

    try:
        db.commit()
    except IntegrityError:
        # Two identical retries raced; the other one created the order.
        db.rollback()
        existing = _order_by_idempotency_key(db, order_in.idempotency_key) if order_in.idempotency_key else None
        if existing:
            return existing
        raise

    # Re-fetch order with relationships
    return db.query(Order).options(*ORDER_DETAIL_OPTIONS).filter(Order.id == new_order.id).first()


def _order_by_idempotency_key(db: Session, key: str) -> Optional[Order]:
    return db.query(Order).options(*ORDER_DETAIL_OPTIONS).filter(Order.idempotency_key == key).first()

@router.get("/{order_id}", response_model=OrderResponse)
def get_order(
    order_id: int,
    token: Optional[str] = Depends(guest_order_token),
    db: Session = Depends(get_db),
):
    """Guest order tracking; requires the order's secret tracking token."""
    return get_order_for_guest(db, order_id, token)

@router.get("", response_model=List[OrderResponse], dependencies=[Depends(require_admin)])
def list_orders(status_filter: Optional[OrderStatus] = None, db: Session = Depends(get_db)):
    """Every order with customer details, so staff only."""
    query = db.query(Order).options(*ORDER_DETAIL_OPTIONS).order_by(Order.id.desc())
    if status_filter:
        query = query.filter(Order.status == status_filter)
    return query.all()
