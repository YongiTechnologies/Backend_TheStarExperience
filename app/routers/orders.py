from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from app.database.connection import get_db
from app.models.order import Order, OrderItem, OrderStatus
from app.models.table import RestaurantTable
from app.models.customer import Customer
from app.models.menu import MenuItem
from app.schemas.order import OrderCreate, OrderResponse

router = APIRouter()

@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(order_in: OrderCreate, db: Session = Depends(get_db)):
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
        total_amount=round(total_amount, 2)
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

    db.commit()

    # Re-fetch order with relationships
    created_order = (
        db.query(Order)
        .options(
            joinedload(Order.customer),
            joinedload(Order.items).joinedload(OrderItem.menu_item),
        )
        .filter(Order.id == new_order.id)
        .first()
    )
    return created_order

@router.get("/{order_id}", response_model=OrderResponse)
def get_order(order_id: int, db: Session = Depends(get_db)):
    order = (
        db.query(Order)
        .options(
            joinedload(Order.customer),
            joinedload(Order.items).joinedload(OrderItem.menu_item),
        )
        .filter(Order.id == order_id)
        .first()
    )
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order with id {order_id} not found"
        )
    return order

@router.get("", response_model=List[OrderResponse])
def list_orders(status_filter: Optional[OrderStatus] = None, db: Session = Depends(get_db)):
    query = (
        db.query(Order)
        .options(
            joinedload(Order.customer),
            joinedload(Order.items).joinedload(OrderItem.menu_item),
        )
        .order_by(Order.id.desc())
    )
    if status_filter:
        query = query.filter(Order.status == status_filter)
    return query.all()
