"""Approved staff devices: one-time setup codes, device tokens, and the check that
guards the staff list and PIN sign-in (see app/models/device.py)."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.device import StaffDevice

PAIRING_CODE_MINUTES = 15
# No 0/O or 1/I, so codes read out loud or copied from a screen don't get mistyped.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_SEEN_EVERY = timedelta(minutes=5)

NOT_APPROVED = "This device isn't set up for staff sign-in. Ask a manager for a setup code."


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc(moment: Optional[datetime]) -> Optional[datetime]:
    return moment.replace(tzinfo=timezone.utc) if moment and moment.tzinfo is None else moment


def new_pairing_code() -> tuple[str, str]:
    """An 8-character code shown as ABCD-EF23 (about 10^12 possibilities, valid 15 minutes),
    and the hash that's stored."""
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}", _hash(raw)


def _normalise(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def start_pairing(device: StaffDevice) -> str:
    code, code_hash = new_pairing_code()
    device.pairing_code_hash = code_hash
    device.pairing_expires_at = datetime.now(timezone.utc) + timedelta(minutes=PAIRING_CODE_MINUTES)
    return code


def pair_with_code(db: Session, code: str) -> Optional[tuple[StaffDevice, str]]:
    """Swap a valid setup code for a device token (returned once; only its hash is kept)."""
    device = (
        db.query(StaffDevice)
        .filter(StaffDevice.pairing_code_hash == _hash(_normalise(code)), StaffDevice.token_hash.is_(None))
        .first()
    )
    now = datetime.now(timezone.utc)
    if not device or not device.pairing_expires_at or _utc(device.pairing_expires_at) <= now:
        return None
    token = secrets.token_urlsafe(32)
    device.token_hash = _hash(token)
    device.pairing_code_hash = None
    device.pairing_expires_at = None
    device.last_seen_at = now
    return device, token


def device_for_token(db: Session, token: Optional[str]) -> Optional[StaffDevice]:
    if not token:
        return None
    return db.query(StaffDevice).filter(StaffDevice.token_hash == _hash(token)).first()


def require_staff_device(
    x_staff_device: Optional[str] = Header(default=None),
    db: Session = Depends(get_db),
) -> StaffDevice:
    """Dependency for the staff list and PIN sign-in: the request must come from an approved device."""
    device = device_for_token(db, x_staff_device)
    if not device:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=NOT_APPROVED)
    now = datetime.now(timezone.utc)
    if not device.last_seen_at or _utc(device.last_seen_at) < now - _SEEN_EVERY:
        device.last_seen_at = now
        db.commit()
    return device
