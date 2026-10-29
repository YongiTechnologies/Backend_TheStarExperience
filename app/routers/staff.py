"""
Staff-facing endpoints: the waiter's board, kitchen and bar tickets, and the
live event stream that keeps every staff screen up to date.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import List

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.activity import log_activity
from app.core.events import bus, emit
from app.core.config import settings
from app.core.push import notify_ready, push_enabled
from app.core.security import effective_role, oauth2_scheme, require_roles, require_staff, resolve_user, token_claims
from app.core.staff_orders import get_staff_order, open_orders, serialize_staff_order, start_shift, STAFF_ORDER_OPTIONS
from app.core.stations import (
    CLOSED_STATUSES,
    Station,
    StationStatus,
    get_station_status,
    set_station_status,
    sync_order_status,
)
from app.database.connection import get_db
from app.models.device import PushSubscription
from app.models.order import Order, OrderStatus
from app.models.user import ShiftStatus, StaffRole, User
from app.routers.auth import profile
from app.schemas.staff import (
    PushConfig,
    PushSubscribe,
    PushUnsubscribe,
    ShiftUpdate,
    StaffOrder,
    StaffProfile,
    StationName,
    StationUpdate,
    WaiterBoard,
)

router = APIRouter()

require_waiter = require_roles(StaffRole.waiter, StaffRole.admin)
RECENTLY_SERVED = timedelta(hours=2)
# Streams end after this long and the client reconnects, so server reloads and
# proxies (e.g. Cloudflare's 100 s idle limit) never hang on a stuck connection.
STREAM_LIFETIME_SECONDS = 60
HEARTBEAT_SECONDS = 15


def _table_sort_key(number: str):
    return (0, int(number)) if number.isdigit() else (1, number)


def _load_order_or_404(db: Session, order_id: int) -> Order:
    order = get_staff_order(db, order_id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    return order


def _ensure_open(order: Order) -> None:
    if order.status in CLOSED_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This order is already closed")


def _ensure_waiter_owns(user: User, order: Order) -> None:
    if effective_role(user) != StaffRole.admin.value and order.waiter_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This order is assigned to another waiter")


# ── Everyone ──────────────────────────────────────────────────────

@router.get("/me", response_model=StaffProfile)
def me(user: User = Depends(require_staff)):
    return profile(user)


@router.put("/me/shift", response_model=StaffProfile)
def set_my_shift(body: ShiftUpdate, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    """Go on break, come back, or end the shift. Waiters off shift receive no new orders."""
    if user.shift != body.shift_status:
        picked_up = []
        if body.shift_status == ShiftStatus.on_shift.value:
            picked_up = start_shift(db, user)
        else:
            user.shift_status = body.shift_status
        log_activity(db, "staff.shift", actor=user, shift_status=body.shift_status, picked_up=picked_up)
        emit(db, "staff.updated", staff_id=user.id)
        if picked_up:
            emit(db, "floor.updated")
        db.commit()
    return profile(user)


# ── Alerts on a locked phone (Web Push) ───────────────────────────

# The server POSTs to subscription endpoints, so only accept the browsers' real push
# services; anything else could point it at internal addresses.
PUSH_SERVICE_HOSTS = ("fcm.googleapis.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com")


def _is_push_service(endpoint: str) -> bool:
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == h or host.endswith("." + h) for h in PUSH_SERVICE_HOSTS)


@router.get("/push/key", response_model=PushConfig)
def push_key(_user: User = Depends(require_staff)):
    """The public key browsers subscribe with (null when push isn't configured)."""
    return {"public_key": settings.VAPID_PUBLIC_KEY if push_enabled() else None}


@router.post("/push", status_code=status.HTTP_204_NO_CONTENT)
def subscribe_push(
    body: PushSubscribe,
    token: str = Depends(oauth2_scheme),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Send this person's alerts to this browser. Re-subscribing a shared phone moves it to whoever is signed in."""
    if not _is_push_service(body.endpoint):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Unsupported push service")
    device_id = token_claims(token).get("dv")
    subscription = db.query(PushSubscription).filter(PushSubscription.endpoint == body.endpoint).first()
    if not subscription:
        subscription = PushSubscription(endpoint=body.endpoint)
        db.add(subscription)
    subscription.user_id = user.id
    subscription.device_id = device_id if isinstance(device_id, int) else None
    subscription.p256dh = body.keys.p256dh
    subscription.auth = body.keys.auth
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/push/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe_push(body: PushUnsubscribe, user: User = Depends(require_staff), db: Session = Depends(get_db)):
    db.query(PushSubscription).filter(
        PushSubscription.endpoint == body.endpoint, PushSubscription.user_id == user.id
    ).delete(synchronize_session=False)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Waiter ────────────────────────────────────────────────────────

@router.get("/waiter/board", response_model=WaiterBoard)
def waiter_board(user: User = Depends(require_roles(StaffRole.waiter)), db: Session = Depends(get_db)):
    """The waiter's tables, their orders in progress, and what they served recently."""
    active = open_orders(db).filter(Order.waiter_id == user.id).all()
    served = (
        db.query(Order)
        .options(*STAFF_ORDER_OPTIONS)
        .filter(
            Order.waiter_id == user.id,
            Order.status == OrderStatus.completed,
            Order.served_at >= datetime.now(timezone.utc) - RECENTLY_SERVED,
        )
        .order_by(Order.served_at.desc())
        .limit(15)
        .all()
    )
    return {
        "me": profile(user),
        "tables": sorted((t.table_number for t in user.tables), key=_table_sort_key),
        "orders": [serialize_staff_order(o) for o in active + served],
    }


@router.post("/orders/{order_id}/accept", response_model=StaffOrder)
def accept_order(order_id: int, user: User = Depends(require_waiter), db: Session = Depends(get_db)):
    """The waiter confirms they're looking after this order."""
    order = _load_order_or_404(db, order_id)
    _ensure_open(order)
    _ensure_waiter_owns(user, order)
    if not order.accepted_at:
        order.accepted_at = datetime.now(timezone.utc)
        log_activity(db, "order.accepted", actor=user, order_id=order.id, table_id=order.table_id)
        emit(db, "order.updated", order_id=order.id, table_number=order.table_number)
        db.commit()
    return serialize_staff_order(_load_order_or_404(db, order_id))


@router.post("/orders/{order_id}/served", response_model=StaffOrder)
def serve_order(order_id: int, user: User = Depends(require_waiter), db: Session = Depends(get_db)):
    """The order reached the table."""
    order = _load_order_or_404(db, order_id)
    _ensure_open(order)
    _ensure_waiter_owns(user, order)
    if order.status != OrderStatus.ready:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This order isn't ready yet")
    now = datetime.now(timezone.utc)
    order.accepted_at = order.accepted_at or now
    order.status = OrderStatus.completed
    order.served_at = now
    log_activity(db, "order.served", actor=user, order_id=order.id, table_id=order.table_id)
    emit(db, "order.updated", order_id=order.id, table_number=order.table_number)
    db.commit()
    return serialize_staff_order(_load_order_or_404(db, order_id))


# ── Kitchen & bar ─────────────────────────────────────────────────

def _require_station(station: StationName, user: User = Depends(require_staff)) -> Station:
    if effective_role(user) not in (station, StaffRole.admin.value):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Only the {station} can do that")
    return Station(station)


@router.get("/station/{station}/tickets", response_model=List[StaffOrder])
def station_tickets(
    station: Station = Depends(_require_station),
    db: Session = Depends(get_db),
):
    """Open orders with lines for this station (queued, preparing, or ready for pick-up)."""
    column = getattr(Order, f"{station.value}_status")
    orders = open_orders(db).filter(column.isnot(None)).all()
    return [serialize_staff_order(o, station=station) for o in orders]


@router.post("/station/{station}/orders/{order_id}", response_model=StaffOrder)
def update_station(
    order_id: int,
    body: StationUpdate,
    station: Station = Depends(_require_station),
    user: User = Depends(require_staff),
    db: Session = Depends(get_db),
):
    """Start preparing, or mark this station's part of the order ready."""
    order = _load_order_or_404(db, order_id)
    _ensure_open(order)
    if get_station_status(order, station) is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"This order has nothing for the {station.value}")
    if get_station_status(order, station) != body.status:
        was_ready = order.status == OrderStatus.ready
        set_station_status(order, station, StationStatus(body.status).value)
        sync_order_status(order)
        if order.status == OrderStatus.ready and not was_ready:
            notify_ready(db, order)
        log_activity(db, f"{station.value}.{body.status}", actor=user, order_id=order.id, table_id=order.table_id)
        emit(db, "order.updated", order_id=order.id, table_number=order.table_number, station=station.value)
        db.commit()
    return serialize_staff_order(_load_order_or_404(db, order_id), station=station)


# ── Live updates ──────────────────────────────────────────────────

@router.get("/events")
async def event_stream(request: Request, token: str = Depends(oauth2_scheme)):
    """Server-Sent Events for staff screens. Each event is a small JSON hint
    ({"type": "order.updated", "order_id": 12}); screens re-fetch what they show."""
    # Authenticate with a short-lived session rather than a request-scoped dependency,
    # so a minute-long stream doesn't hold a database connection.
    session_factory = request.app.dependency_overrides.get(get_db, get_db)
    sessions = session_factory()
    db = next(sessions)
    try:
        user = resolve_user(db, token)
        if effective_role(user) not in {role.value for role in StaffRole}:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Staff only")
    finally:
        sessions.close()

    queue = bus.subscribe()

    async def stream():
        loop = asyncio.get_running_loop()
        deadline = loop.time() + STREAM_LIFETIME_SECONDS
        try:
            yield "retry: 2000\n\n"
            yield 'data: {"type": "hello"}\n\n'
            while loop.time() < deadline:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                    yield f"data: {payload}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
