from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from app.database.connection import get_db
from app.models.device import StaffDevice
from app.models.user import PIN_ROLES, User
from app.schemas.auth import LoginRequest, Token
from app.schemas.staff import DevicePairRequest, DeviceSession, PinLogin, StaffPickerEntry, StaffProfile, StaffSession
from app.core.activity import log_activity
from app.core.devices import pair_with_code, require_staff_device
from app.core.events import emit
from app.core.security import (
    create_access_token,
    create_pin_session_token,
    effective_role,
    require_staff,
    verify_password,
    verify_pin,
)
from app.core.staff_orders import start_shift

router = APIRouter()

# A 4-digit PIN has only 10,000 combinations, so guessing is capped per account:
# every 5 wrong PINs lock it, for 5 minutes and then twice as long each time
# (10, 20, 40 … up to a day) until the right PIN or an admin resets the count.
MAX_PIN_ATTEMPTS = 5
PIN_LOCK_MINUTES = 5
MAX_PIN_LOCK_MINUTES = 24 * 60


def lock_minutes(failures: int) -> int:
    rounds = max(1, failures // MAX_PIN_ATTEMPTS)
    return min(PIN_LOCK_MINUTES * 2 ** min(rounds - 1, 20), MAX_PIN_LOCK_MINUTES)


def _wait_text(minutes: int) -> str:
    if minutes >= 90:
        hours = round(minutes / 60)
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{minutes} minute{'s' if minutes != 1 else ''}"
_PIN_ROLE_VALUES = [role.value for role in PIN_ROLES]


def authenticate(username: str, password: str, db: Session) -> Token:
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.active or not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(subject=user.username)
    return Token(access_token=access_token, token_type="bearer")


@router.post("/login", response_model=Token)
def login(credentials: LoginRequest, db: Session = Depends(get_db)):
    """JSON login endpoint for frontend clients."""
    return authenticate(credentials.username, credentials.password, db)


@router.post("/token", response_model=Token)
def login_for_swagger(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """OAuth2 password-flow endpoint used by Swagger's Authorize dialog."""
    return authenticate(form_data.username, form_data.password, db)


def profile(user: User) -> StaffProfile:
    return StaffProfile(id=user.id, display_name=user.name, role=effective_role(user), shift_status=user.shift)


@router.get("/staff", response_model=List[StaffPickerEntry])
def staff_picker(_device: StaffDevice = Depends(require_staff_device), db: Session = Depends(get_db)):
    """Who can sign in with a PIN: the name list staff tap before entering their PIN.
    Only approved devices get it, so staff names aren't public."""
    users = (
        db.query(User)
        .filter(User.pin_hash.isnot(None), User.role.in_(_PIN_ROLE_VALUES))
        .order_by(User.role, User.display_name)
        .all()
    )
    return [StaffPickerEntry(id=u.id, display_name=u.name, role=u.role) for u in users if u.active]


def _as_utc(moment: datetime) -> datetime:
    # SQLite returns naive datetimes; everything is stored in UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


@router.post("/pin", response_model=StaffSession)
def pin_login(body: PinLogin, device: StaffDevice = Depends(require_staff_device), db: Session = Depends(get_db)):
    incorrect = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Incorrect PIN")
    user = db.get(User, body.staff_id)
    if not user or not user.active or not user.pin_hash or user.role not in _PIN_ROLE_VALUES:
        raise incorrect

    now = datetime.now(timezone.utc)
    if user.locked_until and _as_utc(user.locked_until) > now:
        minutes = max(1, round((_as_utc(user.locked_until) - now).total_seconds() / 60))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many wrong PINs. Try again in {_wait_text(minutes)}, or ask a manager to unlock you.",
        )

    if not verify_pin(body.pin, user.pin_hash):
        attempts = (user.failed_pin_attempts or 0) + 1
        user.failed_pin_attempts = attempts
        if attempts % MAX_PIN_ATTEMPTS == 0:
            minutes = lock_minutes(attempts)
            user.locked_until = now + timedelta(minutes=minutes)
            emit(db, "staff.updated", staff_id=user.id)
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many wrong PINs. Try again in {_wait_text(minutes)}, or ask a manager to unlock you.",
            )
        db.commit()
        raise incorrect

    user.failed_pin_attempts = 0
    user.locked_until = None
    # Signing in starts the shift, so the waiter immediately receives their tables' orders.
    picked_up = start_shift(db, user, new_session=True)
    emit(db, "staff.updated", staff_id=user.id)
    if picked_up:
        emit(db, "floor.updated")
    db.commit()
    return StaffSession(access_token=create_pin_session_token(user, device), user=profile(user))


@router.get("/me", response_model=StaffProfile)
def me(user: User = Depends(require_staff)):
    return profile(user)


@router.post("/devices/pair", response_model=DeviceSession)
def pair_device(body: DevicePairRequest, db: Session = Depends(get_db)):
    """Set up this phone or tablet for staff sign-in with the code an admin created."""
    paired = pair_with_code(db, body.code)
    if not paired:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That code didn't work. Check it, or ask a manager for a new one (codes last 15 minutes).",
        )
    device, token = paired
    log_activity(db, "device.paired", device_id=device.id, name=device.name)
    db.commit()
    return DeviceSession(device_token=token, id=device.id, name=device.name)
