"""
Admin: staff accounts, table assignment and the floor overview.

Assignment is per table: a table's waiter receives every new order from it.
Admins can reassign a table (optionally moving its open orders), hand all of a
waiter's tables to someone else at shift change, or override a single order.
"""
import json
import secrets
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.core.activity import log_activity
from app.core.devices import start_pairing
from app.core.events import emit
from app.core.push import forget_subscriptions, notify_order_for_waiter
from app.core.security import get_password_hash, hash_pin, require_admin
from app.core.staff_orders import (
    get_staff_order,
    is_available_waiter,
    open_orders,
    serialize_staff_order,
    start_shift,
    utc,
)
from app.core.stations import CLOSED_STATUSES
from app.database.connection import get_db
from app.models.activity import ActivityEvent
from app.models.device import StaffDevice
from app.models.order import Order
from app.models.table import RestaurantTable
from app.models.user import PIN_ROLES, ShiftStatus, StaffRole, User
from app.schemas.staff import (
    DeviceCreate,
    DevicePairing,
    FloorView,
    Handover,
    OrderWaiterUpdate,
    StaffCreate,
    StaffMember,
    StaffOrder,
    StaffDeviceOut,
    StaffUpdate,
    TableWaiterUpdate,
)

router = APIRouter(dependencies=[Depends(require_admin)])

_PIN_ROLE_VALUES = [role.value for role in PIN_ROLES]


def _guessable(pin: str) -> bool:
    """PINs anyone would try first: 0000, 1212, 1234, 9876, 2580 (keypad column)…"""
    steps = {int(b) - int(a) for a, b in zip(pin, pin[1:])}
    return (
        len(set(pin)) == 1
        or steps in ({1}, {-1})
        or pin[:2] == pin[2:]
        or pin in {"2580", "0852", "1122", "6969", "1004", "2000", "1999"}
    )


def _ensure_strong_pin(pin: Optional[str]) -> None:
    if pin is not None and _guessable(pin):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="That PIN is too easy to guess. Avoid repeats and runs like 1111, 1212 or 1234.",
        )


def _ensure_unique_name(db: Session, name: str, exclude_id: Optional[int] = None) -> None:
    """Two active people with the same name can't tell which button on the sign-in screen is theirs."""
    clash = (
        db.query(User)
        .filter(
            func.lower(User.display_name) == name.strip().lower(),
            User.role.in_(_PIN_ROLE_VALUES),
            User.is_active.isnot(False),
            User.id != (exclude_id or 0),
        )
        .first()
    )
    if clash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Someone active is already called {clash.name}. Add an initial or surname so staff can tell them apart.",
        )


def _is_locked(user: User) -> bool:
    return bool(user.locked_until and utc(user.locked_until) > datetime.now(timezone.utc))


def _table_sort_key(table: RestaurantTable):
    number = table.table_number
    return (0, int(number)) if number.isdigit() else (1, number)


def _open_order_counts(db: Session) -> dict[int, int]:
    rows = (
        db.query(Order.waiter_id, func.count(Order.id))
        .filter(Order.waiter_id.isnot(None), Order.status.notin_(CLOSED_STATUSES))
        .group_by(Order.waiter_id)
        .all()
    )
    return dict(rows)


def _staff_or_404(db: Session, staff_id: int) -> User:
    user = db.query(User).options(joinedload(User.tables)).filter(User.id == staff_id).first()
    if not user or user.role not in _PIN_ROLE_VALUES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff member not found")
    return user


def _waiter_or_422(db: Session, waiter_id: Optional[int]) -> Optional[User]:
    if waiter_id is None:
        return None
    waiter = db.get(User, waiter_id)
    if not waiter or waiter.role != StaffRole.waiter.value or not waiter.active:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Choose an active waiter")
    return waiter


def _reassign_orders(db: Session, orders, waiter: Optional[User], actor: User, reason: str) -> list[int]:
    """Point open orders at a new waiter (they must accept again), noting it on each order's timeline."""
    moved = []
    for order in orders:
        if order.status in CLOSED_STATUSES or order.waiter_id == (waiter.id if waiter else None):
            continue
        log_activity(
            db, "order.assigned", actor=actor, order_id=order.id, table_id=order.table_id, reason=reason,
            from_staff=order.waiter.name if order.waiter else None, to_staff=waiter.name if waiter else None,
        )
        order.waiter_id = waiter.id if waiter else None
        order.waiter = waiter
        order.assigned_at = datetime.now(timezone.utc) if waiter else None
        order.accepted_at = None
        if waiter:
            notify_order_for_waiter(db, order, waiter.id, moved=True)
        moved.append(order.id)
    return moved


def _member(user: User, counts: dict[int, int]) -> dict:
    return {
        "id": user.id,
        "display_name": user.name,
        "role": user.role,
        "is_active": user.active,
        "shift_status": user.shift,
        "has_pin": bool(user.pin_hash),
        "locked": _is_locked(user),
        "tables": [t.table_number for t in sorted(user.tables, key=_table_sort_key)],
        "open_orders": counts.get(user.id, 0),
    }


# ── Staff accounts ────────────────────────────────────────────────

@router.get("/staff", response_model=List[StaffMember])
def list_staff(db: Session = Depends(get_db)):
    users = (
        db.query(User)
        .options(joinedload(User.tables))
        .filter(User.role.in_(_PIN_ROLE_VALUES))
        .order_by(User.role, User.display_name)
        .all()
    )
    counts = _open_order_counts(db)
    return [_member(u, counts) for u in users]


@router.post("/staff", response_model=StaffMember, status_code=status.HTTP_201_CREATED)
def create_staff(body: StaffCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _ensure_strong_pin(body.pin)
    _ensure_unique_name(db, body.display_name)
    user = User(
        # Internal identifier for tokens; staff only ever see their display name.
        username=f"{body.role}-{secrets.token_hex(4)}",
        # PIN-only account: a random, never-shared password so password login can't succeed.
        hashed_password=get_password_hash(secrets.token_urlsafe(32)),
        display_name=body.display_name,
        role=body.role,
        pin_hash=hash_pin(body.pin),
        is_active=True,
        shift_status=ShiftStatus.off.value,
        failed_pin_attempts=0,
    )
    db.add(user)
    db.flush()
    log_activity(db, "staff.created", actor=admin, staff_id=user.id, role=body.role)
    emit(db, "staff.updated", staff_id=user.id)
    db.commit()
    return _member(_staff_or_404(db, user.id), {})


@router.patch("/staff/{staff_id}", response_model=StaffMember)
def update_staff(staff_id: int, body: StaffUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = _staff_or_404(db, staff_id)
    changes = body.model_dump(exclude_unset=True)
    _ensure_strong_pin(body.pin)
    renaming = body.display_name is not None and body.display_name.lower() != user.name.lower()
    reactivating = body.is_active is True and not user.active
    if reactivating or (renaming and user.active):
        _ensure_unique_name(db, body.display_name or user.name, exclude_id=user.id)
    if "display_name" in changes:
        user.display_name = body.display_name
    if "pin" in changes or body.unlock:
        user.failed_pin_attempts = 0
        user.locked_until = None
    if "pin" in changes:
        # The new PIN signs out every device using the old one; they sign in again to restart the shift.
        user.pin_hash = hash_pin(body.pin)
        user.shift_status = ShiftStatus.off.value
        forget_subscriptions(db, user_id=user.id)
    if "shift_status" in changes:
        if body.shift_status == ShiftStatus.on_shift.value:
            changes["picked_up"] = start_shift(db, user)
        else:
            user.shift_status = body.shift_status
    if "role" in changes and body.role != user.role:
        # Signs out their devices (the role is part of the session), so they land on the right screen.
        user.role = body.role
        user.shift_status = ShiftStatus.off.value
        forget_subscriptions(db, user_id=user.id)
        if body.role != StaffRole.waiter.value:
            changes["unassigned"] = _release_waiter(db, user, admin)
    if "is_active" in changes:
        user.is_active = body.is_active
        if not body.is_active:
            user.shift_status = ShiftStatus.off.value
            forget_subscriptions(db, user_id=user.id)
            changes["unassigned"] = _release_waiter(db, user, admin)
    changes.pop("pin", None)  # never log PINs
    log_activity(db, "staff.updated", actor=admin, staff_id=user.id, **changes)
    emit(db, "staff.updated", staff_id=user.id)
    db.commit()
    return _member(_staff_or_404(db, staff_id), _open_order_counts(db))


def _release_waiter(db: Session, user: User, admin: User) -> dict:
    """Take tables and open orders away from someone who can no longer serve them."""
    tables = [t.table_number for t in user.tables]
    for table in list(user.tables):
        table.waiter_id = None
    moved = _reassign_orders(db, open_orders(db).filter(Order.waiter_id == user.id).all(), None, admin, "released")
    emit(db, "floor.updated")
    return {"tables": tables, "orders": moved}


@router.post("/staff/{staff_id}/handover", response_model=StaffMember)
def handover(staff_id: int, body: Handover, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Shift change: move all of a waiter's tables and open orders to another waiter (or unassign them)."""
    user = _staff_or_404(db, staff_id)
    target = _waiter_or_422(db, body.to_staff_id)
    if target and target.id == user.id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Choose a different waiter")
    tables = [t.table_number for t in user.tables]
    for table in list(user.tables):
        table.waiter_id = target.id if target else None
    moved = _reassign_orders(db, open_orders(db).filter(Order.waiter_id == user.id).all(), target, admin, "handover")
    log_activity(db, "staff.handover", actor=admin, staff_id=user.id, to_staff_id=target.id if target else None, tables=tables, orders=moved)
    emit(db, "floor.updated")
    db.commit()
    return _member(_staff_or_404(db, staff_id), _open_order_counts(db))


# ── Floor ─────────────────────────────────────────────────────────

@router.get("/floor", response_model=FloorView)
def floor(db: Session = Depends(get_db)):
    """Every table with its waiter and open orders, the waiters on the floor, and orders nobody has."""
    tables = sorted(db.query(RestaurantTable).options(joinedload(RestaurantTable.waiter)).all(), key=_table_sort_key)
    active_orders = open_orders(db).all()
    by_table: dict[int, list] = {}
    unassigned = []
    for order in active_orders:
        by_table.setdefault(order.table_id, []).append(order)
        if order.waiter_id is None:
            unassigned.append(serialize_staff_order(order))

    waiters = (
        db.query(User)
        .options(joinedload(User.tables))
        .filter(User.role == StaffRole.waiter.value)
        .order_by(User.display_name)
        .all()
    )
    counts = _open_order_counts(db)
    return {
        "tables": [
            {
                "id": t.id,
                "table_number": t.table_number,
                "waiter": {"id": t.waiter.id, "display_name": t.waiter.name} if t.waiter else None,
                "waiter_available": is_available_waiter(t.waiter),
                "open_orders": [serialize_staff_order(o) for o in by_table.get(t.id, [])],
            }
            for t in tables
        ],
        "waiters": [
            {
                "id": w.id,
                "display_name": w.name,
                "shift_status": w.shift,
                "tables": [t.table_number for t in sorted(w.tables, key=_table_sort_key)],
                "open_orders": counts.get(w.id, 0),
            }
            for w in waiters
            if w.active
        ],
        "unassigned_orders": unassigned,
    }


@router.put("/tables/{table_id}/waiter")
def assign_table(table_id: int, body: TableWaiterUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Give a table to a waiter (or unassign it). New orders from the table go to them;
    with move_open_orders, orders already in progress move too."""
    table = db.get(RestaurantTable, table_id)
    if not table:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Table not found")
    waiter = _waiter_or_422(db, body.waiter_id)
    previous = table.waiter_id
    table.waiter_id = waiter.id if waiter else None
    moved = []
    if body.move_open_orders:
        moved = _reassign_orders(db, open_orders(db).filter(Order.table_id == table.id).all(), waiter, admin, "table")
    log_activity(
        db, "table.assigned", actor=admin, table_id=table.id,
        table_number=table.table_number, from_staff_id=previous, to_staff_id=table.waiter_id, orders=moved,
    )
    emit(db, "floor.updated", table_number=table.table_number)
    db.commit()
    return {"table_id": table.id, "table_number": table.table_number, "waiter_id": table.waiter_id, "moved_orders": moved}


@router.put("/orders/{order_id}/waiter", response_model=StaffOrder)
def assign_order(order_id: int, body: OrderWaiterUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Override who handles one order (the table's assignment is unchanged)."""
    order = get_staff_order(db, order_id)
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")
    if order.status in CLOSED_STATUSES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This order is already closed")
    waiter = _waiter_or_422(db, body.waiter_id)
    _reassign_orders(db, [order], waiter, admin, "manual")
    emit(db, "order.updated", order_id=order.id, table_number=order.table_number)
    db.commit()
    return serialize_staff_order(get_staff_order(db, order_id))


@router.get("/orders/{order_id}/activity")
def order_activity(order_id: int, db: Session = Depends(get_db)):
    """The order's timeline: placed, routed, accepted, prepared, served, reassigned."""
    events = (
        db.query(ActivityEvent)
        .options(joinedload(ActivityEvent.actor))
        .filter(ActivityEvent.order_id == order_id)
        .order_by(ActivityEvent.created_at.asc(), ActivityEvent.id.asc())
        .all()
    )
    return [
        {
            "action": e.action,
            "actor": e.actor.name if e.actor else None,
            "created_at": utc(e.created_at),
            "detail": json.loads(e.detail) if e.detail else None,
        }
        for e in events
    ]


# ── Approved devices ──────────────────────────────────────────────

def _device_or_404(db: Session, device_id: int) -> StaffDevice:
    device = db.get(StaffDevice, device_id)
    if not device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    return device


def _pairing(device: StaffDevice, code: str) -> dict:
    return {"id": device.id, "name": device.name, "code": code, "expires_at": utc(device.pairing_expires_at)}


@router.get("/devices", response_model=List[StaffDeviceOut])
def list_devices(db: Session = Depends(get_db)):
    """Phones and tablets approved for staff sign-in, plus ones still waiting for their code."""
    # Setup codes that ran out unused leave nothing behind.
    db.query(StaffDevice).filter(
        StaffDevice.token_hash.is_(None), StaffDevice.pairing_expires_at < datetime.now(timezone.utc)
    ).delete(synchronize_session=False)
    db.commit()
    devices = db.query(StaffDevice).order_by(StaffDevice.created_at.asc(), StaffDevice.id.asc()).all()
    return [
        {
            "id": d.id,
            "name": d.name,
            "paired": d.paired,
            "created_at": utc(d.created_at),
            "last_seen_at": utc(d.last_seen_at),
            "pairing_expires_at": utc(d.pairing_expires_at),
        }
        for d in devices
    ]


@router.post("/devices", response_model=DevicePairing, status_code=status.HTTP_201_CREATED)
def add_device(body: DeviceCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Approve a new phone or tablet: returns a one-time code to enter on it within 15 minutes."""
    device = StaffDevice(name=body.name, created_by_id=admin.id)
    code = start_pairing(device)
    db.add(device)
    db.flush()
    log_activity(db, "device.added", actor=admin, device_id=device.id, name=device.name)
    db.commit()
    return _pairing(device, code)


@router.post("/devices/{device_id}/code", response_model=DevicePairing)
def new_device_code(device_id: int, db: Session = Depends(get_db)):
    """A fresh code for a device that hasn't been set up yet (e.g. the first one expired)."""
    device = _device_or_404(db, device_id)
    if device.paired:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This device is already set up")
    code = start_pairing(device)
    db.commit()
    return _pairing(device, code)


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_device(device_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Revoke a lost or replaced device: whoever is signed in on it is signed out, and it stops getting alerts."""
    device = _device_or_404(db, device_id)
    forget_subscriptions(db, device_id=device.id)
    log_activity(db, "device.removed", actor=admin, device_id=device.id, name=device.name)
    db.delete(device)
    # Screens refresh on this, so a session on the revoked device ends straight away.
    emit(db, "staff.updated")
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
