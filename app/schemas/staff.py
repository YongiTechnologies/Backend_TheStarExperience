from datetime import datetime
from typing import Annotated, List, Literal, Optional

from pydantic import BaseModel, Field, StringConstraints

Pin = Annotated[str, StringConstraints(pattern=r"^\d{4}$")]
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
PinRole = Literal["waiter", "kitchen", "bar"]
ShiftValue = Literal["on_shift", "on_break", "off"]
StationName = Literal["kitchen", "bar"]


# ── Auth ──────────────────────────────────────────────────────────

class StaffPickerEntry(BaseModel):
    id: int
    display_name: str
    role: str


class PinLogin(BaseModel):
    staff_id: int
    pin: Pin


class StaffProfile(BaseModel):
    id: int
    display_name: str
    role: str
    shift_status: str


class StaffSession(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: StaffProfile


# ── Staff management (admin) ──────────────────────────────────────

class StaffCreate(BaseModel):
    display_name: DisplayName
    role: PinRole
    pin: Pin


class StaffUpdate(BaseModel):
    display_name: Optional[DisplayName] = None
    role: Optional[PinRole] = None
    pin: Optional[Pin] = None
    is_active: Optional[bool] = None
    shift_status: Optional[ShiftValue] = None
    # Clear a lock from too many wrong PINs (also cleared by setting a new PIN).
    unlock: Optional[bool] = None


class StaffMember(BaseModel):
    id: int
    display_name: str
    role: str
    is_active: bool
    shift_status: str
    has_pin: bool
    locked: bool = False
    tables: List[str]
    open_orders: int


class ShiftUpdate(BaseModel):
    shift_status: ShiftValue


class Handover(BaseModel):
    to_staff_id: Optional[int] = Field(default=None, description="Waiter taking over; omit to unassign the tables")


# ── Assignment ────────────────────────────────────────────────────

class TableWaiterUpdate(BaseModel):
    waiter_id: Optional[int] = None
    move_open_orders: bool = True


class OrderWaiterUpdate(BaseModel):
    waiter_id: Optional[int] = None


# ── Orders as staff see them ──────────────────────────────────────

class StaffOrderLine(BaseModel):
    name: str
    quantity: int
    station: StationName


class StaffRef(BaseModel):
    id: int
    display_name: str


class StaffOrder(BaseModel):
    id: int
    table_number: Optional[str]
    customer_name: str
    customer_phone: str
    notes: Optional[str]
    status: str
    total: float
    payment_status: str
    payment_provider: Optional[str]
    payment_method: Optional[str]
    waiter: Optional[StaffRef]
    assigned_at: Optional[datetime] = None
    accepted_at: Optional[datetime]
    kitchen_status: Optional[str]
    bar_status: Optional[str]
    ready_at: Optional[datetime]
    served_at: Optional[datetime]
    created_at: Optional[datetime]
    items: List[StaffOrderLine]


class StationUpdate(BaseModel):
    status: Literal["preparing", "ready"]


class WaiterBoard(BaseModel):
    me: StaffProfile
    tables: List[str]
    orders: List[StaffOrder]


# ── Floor (admin) ─────────────────────────────────────────────────

class FloorTable(BaseModel):
    id: int
    table_number: str
    waiter: Optional[StaffRef]
    waiter_available: bool
    open_orders: List[StaffOrder]


class FloorWaiter(BaseModel):
    id: int
    display_name: str
    shift_status: str
    tables: List[str]
    open_orders: int


class FloorView(BaseModel):
    tables: List[FloorTable]
    waiters: List[FloorWaiter]
    unassigned_orders: List[StaffOrder]


# ── Approved devices ──────────────────────────────────────────────

DeviceName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


class DeviceCreate(BaseModel):
    name: DeviceName


class DevicePairing(BaseModel):
    """A new device's one-time setup code (shown to the admin once)."""
    id: int
    name: str
    code: str
    expires_at: datetime


class StaffDeviceOut(BaseModel):
    id: int
    name: str
    paired: bool
    created_at: Optional[datetime]
    last_seen_at: Optional[datetime]
    pairing_expires_at: Optional[datetime]


class DevicePairRequest(BaseModel):
    code: Annotated[str, StringConstraints(strip_whitespace=True, min_length=4, max_length=20)]


class DeviceSession(BaseModel):
    device_token: str
    id: int
    name: str


# ── Push notifications ────────────────────────────────────────────

class PushConfig(BaseModel):
    public_key: Optional[str]


class PushKeys(BaseModel):
    p256dh: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    auth: Annotated[str, StringConstraints(min_length=1, max_length=100)]


class PushSubscribe(BaseModel):
    endpoint: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    keys: PushKeys


class PushUnsubscribe(BaseModel):
    endpoint: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
