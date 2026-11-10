"""
Admin side of the Loyverse sync: checking the connection, linking menu items to
Loyverse items, viewing receipts and retrying orders whose receipt didn't go through.
"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core import loyverse
from app.core.config import settings
from app.core.events import emit
from app.core.security import require_admin
from app.database.connection import get_db
from app.models.menu import MenuItem
from app.models.order import Order
from app.models.payment import Payment, PaymentStatus
from app.schemas.loyverse import (
    LoyverseLinkReport,
    LoyverseReceiptResponse,
    LoyverseStatusResponse,
    LoyverseSyncResponse,
    LoyverseVariantResponse,
    MenuItemLinkResponse,
    MenuItemLinkUpdate,
    SimulatedOutageUpdate,
)

router = APIRouter(dependencies=[Depends(require_admin)])


def _client(db: Session):
    try:
        return loyverse.get_client(db)
    except loyverse.LoyverseError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _live_client(db: Session) -> loyverse.LiveLoyverse:
    if settings.LOYVERSE_MODE != "live":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Set LOYVERSE_MODE=live to link items")
    return _client(db)


def _loyverse_call(call):
    """Run a Loyverse API call, turning its failures into a 502 the dashboard can show."""
    try:
        return call()
    except loyverse.LoyverseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


def _sync_response(order: Order) -> LoyverseSyncResponse:
    return LoyverseSyncResponse(
        order_id=order.id,
        loyverse_receipt_number=order.payment.loyverse_receipt_number,
        loyverse_sync_status=order.payment.loyverse_sync_status,
        loyverse_sync_error=order.payment.loyverse_sync_error,
    )


@router.get("/status", response_model=LoyverseStatusResponse)
def get_status(db: Session = Depends(get_db)):
    """Checks the connection too: the store, and that both payment types exist in Loyverse."""
    loyverse.clear_cache()
    result = {
        "mode": settings.LOYVERSE_MODE,
        "connected": False,
        "payment_types": {method: loyverse.payment_type_name(method) for method in ("momo", "card")},
        "menu_items": db.query(MenuItem).count(),
        "linked_menu_items": db.query(MenuItem).filter(MenuItem.loyverse_variant_id.isnot(None)).count(),
        "simulated_outage": loyverse.simulated_outage(),
    }
    try:
        client = loyverse.get_client(db)
        result["store_id"] = client.store_id()
        if settings.LOYVERSE_MODE == "live":
            result["store_name"] = client.store_name()
            for method in ("momo", "card"):
                client.payment_type_id(method)
        result["connected"] = True
    except loyverse.LoyverseError as exc:
        result["error"] = str(exc)
    return result


@router.put("/simulation/outage", response_model=LoyverseStatusResponse)
def set_simulated_outage(update: SimulatedOutageUpdate, db: Session = Depends(get_db)):
    if settings.LOYVERSE_MODE != "simulated":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Loyverse isn't in simulated mode")
    loyverse.set_simulated_outage(update.enabled)
    return get_status(db)


@router.get("/receipts", response_model=List[LoyverseReceiptResponse])
def list_receipts(limit: int = 100, db: Session = Depends(get_db)):
    """Receipts this app sent to Loyverse, newest first."""
    client = _client(db)
    return _loyverse_call(lambda: client.list_receipts(min(max(limit, 1), 250)))


@router.get("/items", response_model=List[LoyverseVariantResponse])
def list_loyverse_items(db: Session = Depends(get_db)):
    """Every Loyverse item variant, and which menu item (if any) it's linked to."""
    client = _live_client(db)
    items = _loyverse_call(client.items)
    linked = {
        variant_id: menu_item_id
        for menu_item_id, variant_id in db.query(MenuItem.id, MenuItem.loyverse_variant_id)
        if variant_id
    }
    return [
        {**loyverse._variant_summary([item], variant), "linked_menu_item_id": linked.get(variant["variant_id"])}
        for item in items
        for variant in item.get("variants") or []
    ]


@router.post("/items/link", response_model=LoyverseLinkReport)
def link_items_by_name(overwrite: bool = False, db: Session = Depends(get_db)):
    """Link menu items to Loyverse items with the same name. Already-linked items are
    kept unless overwrite=true. Read the report: anything not linked can't be sold
    through Loyverse until it's linked by hand."""
    client = _live_client(db)
    report = _loyverse_call(lambda: loyverse.link_menu_items(db, client, overwrite=overwrite))
    db.commit()
    return report


@router.put("/menu-items/{menu_item_id}/link", response_model=MenuItemLinkResponse)
def link_menu_item(menu_item_id: int, update: MenuItemLinkUpdate, db: Session = Depends(get_db)):
    """Link one menu item to a Loyverse variant by hand (e.g. names differ, or the item has sizes)."""
    menu_item = db.get(MenuItem, menu_item_id)
    if not menu_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Menu item not found")
    if update.variant_id:
        client = _live_client(db)
        _loyverse_call(lambda: client.get_variant(update.variant_id))
    menu_item.loyverse_variant_id = update.variant_id
    db.commit()
    return MenuItemLinkResponse(
        menu_item_id=menu_item.id, name=menu_item.name, loyverse_variant_id=menu_item.loyverse_variant_id
    )


@router.post("/orders/{order_id}/sync", response_model=LoyverseSyncResponse)
def sync_order(order_id: int, db: Session = Depends(get_db)):
    order = db.get(Order, order_id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if not order.payment or order.payment.status != PaymentStatus.paid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only paid orders go to Loyverse")
    loyverse.sync_order_receipt(db, order)
    emit(db, "order.updated", order_id=order.id, table_number=order.table_number)
    db.commit()
    return _sync_response(order)


@router.post("/sync-failed", response_model=List[LoyverseSyncResponse])
def sync_failed_orders(db: Session = Depends(get_db)):
    """Retry every paid order whose receipt hasn't reached Loyverse, e.g. after an outage."""
    orders = (
        db.query(Order)
        .join(Payment)
        .filter(Payment.status == PaymentStatus.paid, Payment.loyverse_receipt_number.is_(None))
        .order_by(Order.id)
        .all()
    )
    for order in orders:
        loyverse.sync_order_receipt(db, order)
        emit(db, "order.updated", order_id=order.id, table_number=order.table_number)
    db.commit()
    return [_sync_response(order) for order in orders]
