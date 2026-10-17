"""Guest access to a single order, proven by its secret tracking token."""
import secrets
from typing import Optional

from fastapi import Header, HTTPException, Query, status
from sqlalchemy.orm import Session, joinedload

from app.models.order import Order, OrderItem

# Everything a guest-facing order response needs, loaded in one query.
ORDER_DETAIL_OPTIONS = (
    joinedload(Order.customer),
    joinedload(Order.table),
    joinedload(Order.payment),
    joinedload(Order.items).joinedload(OrderItem.menu_item),
)


def guest_order_token(
    x_order_token: Optional[str] = Header(default=None, description="Order tracking token (preferred: keeps it out of URLs and logs)"),
    token: Optional[str] = Query(default=None, description="Order tracking token (fallback for links)"),
) -> Optional[str]:
    """FastAPI dependency: the tracking token from the X-Order-Token header or ?token=."""
    return x_order_token or token


def new_tracking_token() -> str:
    return secrets.token_urlsafe(24)


def token_matches(order: Optional[Order], token: Optional[str]) -> bool:
    if not order or not order.tracking_token or not token:
        return False
    # Compare bytes: compare_digest rejects non-ASCII str input with a TypeError.
    return secrets.compare_digest(order.tracking_token.encode(), token.encode())


def get_order_for_guest(db: Session, order_id: int, token: Optional[str]) -> Order:
    """Order IDs are sequential, so knowing one proves nothing; the token does.
    A wrong or missing token is indistinguishable from a missing order."""
    order = db.query(Order).options(*ORDER_DETAIL_OPTIONS).filter(Order.id == order_id).first()
    if not token_matches(order, token):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order with id {order_id} not found",
        )
    return order
