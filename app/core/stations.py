"""
Kitchen and bar stations.

Each order line goes to one station by its menu category: food to the kitchen,
everything else (cocktails, beer, wine, spirits, soft drinks) to the bar. An
order carries a status per station it needs; the order itself becomes
"preparing" once any station starts and "ready" once every station involved is
done. The waiter then serves it.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import inspect

from app.models.order import Order, OrderStatus


class Station(str, Enum):
    kitchen = "kitchen"
    bar = "bar"


class StationStatus(str, Enum):
    queued = "queued"
    preparing = "preparing"
    ready = "ready"


KITCHEN_CATEGORIES = {"food"}

# Orders that are finished from the floor's point of view.
CLOSED_STATUSES = (OrderStatus.completed, OrderStatus.cancelled)


def station_for_category(category_name: Optional[str]) -> Station:
    return Station.kitchen if (category_name or "").lower() in KITCHEN_CATEGORIES else Station.bar


def station_for_item(order_item) -> Station:
    if order_item.station in (Station.kitchen.value, Station.bar.value):
        return Station(order_item.station)
    # Lines ordered before stations were recorded: go by the item's current category.
    menu_item = order_item.menu_item
    category = menu_item.category.name if menu_item and menu_item.category else None
    return station_for_category(category)


def station_column(station: Station) -> str:
    return f"{station.value}_status"


def get_station_status(order: Order, station: Station) -> Optional[str]:
    return getattr(order, station_column(station))


def set_station_status(order: Order, station: Station, value: Optional[str]) -> None:
    setattr(order, station_column(station), value)


def initial_station_statuses(order: Order) -> None:
    """Queue the order at every station it has items for."""
    needed = {station_for_item(item) for item in order.items}
    for station in Station:
        set_station_status(order, station, StationStatus.queued.value if station in needed else None)


def sync_order_status(order: Order) -> None:
    """Derive the order's status from its stations (never reopens closed orders)."""
    if order.status in CLOSED_STATUSES:
        return
    statuses = [s for s in (get_station_status(order, st) for st in Station) if s]
    if statuses and all(s == StationStatus.ready.value for s in statuses):
        if order.status != OrderStatus.ready:
            order.status = OrderStatus.ready
            order.ready_at = datetime.now(timezone.utc)
    elif any(s in (StationStatus.preparing.value, StationStatus.ready.value) for s in statuses):
        order.status = OrderStatus.preparing
    elif order.status != OrderStatus.paid:
        order.status = OrderStatus.pending


def display_status(order: Order) -> str:
    """The status names the frontend uses (received/preparing/ready/completed/cancelled)."""
    if order.status in (OrderStatus.pending, OrderStatus.paid):
        return "received"
    return order.status.value


def backfill_station_statuses(db) -> int:
    """Orders placed before kitchen/bar tracking have no station statuses, so they'd never
    reach the station screens or become ready to serve. Give open ones statuses that match
    the stage they're at. Safe to run on every start: it only touches such orders."""
    if not inspect(db.get_bind()).has_table("orders"):
        return 0  # empty database: seed.py sets it up
    orders = (
        db.query(Order)
        .filter(Order.status.notin_(CLOSED_STATUSES), Order.kitchen_status.is_(None), Order.bar_status.is_(None))
        .all()
    )
    stage = {OrderStatus.preparing: StationStatus.preparing.value, OrderStatus.ready: StationStatus.ready.value}
    for order in orders:
        initial_station_statuses(order)
        if order.status in stage:
            for station in Station:
                if get_station_status(order, station) is not None:
                    set_station_status(order, station, stage[order.status])
    db.commit()
    return len(orders)
