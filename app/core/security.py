from datetime import datetime, timedelta, timezone
from typing import Optional, Any, Union
import bcrypt

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.connection import get_db
from app.models.device import StaffDevice
from app.models.user import PIN_ROLES, StaffRole, User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))
    except (ValueError, TypeError):
        return False

def get_password_hash(password: str) -> str:
    password_bytes = password.encode("utf-8")
    if len(password_bytes) > 72:
        raise ValueError("Password must be 72 bytes or fewer")
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")

def create_access_token(
    subject: Union[str, Any], expires_delta: Optional[timedelta] = None, claims: Optional[dict] = None
) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {**(claims or {}), "exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def _credentials_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _token_payload(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError as exc:
        raise _credentials_exception() from exc
    if not isinstance(payload.get("sub"), str) or not payload["sub"]:
        raise _credentials_exception()
    return payload


def username_from_token(token: str) -> str:
    return _token_payload(token)["sub"]


def get_current_username(token: str = Depends(oauth2_scheme)) -> str:
    """Validate a bearer token and return its authenticated username."""
    return username_from_token(token)


def effective_role(user: User) -> Optional[str]:
    """The user's role. The account named by ADMIN_USERNAME is always an admin,
    so the original admin keeps working without a role set in the database."""
    if user.username == settings.ADMIN_USERNAME:
        return StaffRole.admin.value
    return user.role


PIN_ROLE_VALUES = {role.value for role in PIN_ROLES}


def token_claims(token: str) -> dict:
    """The verified claims of a token (raises 401 if it's invalid or expired)."""
    return _token_payload(token)


def resolve_user(db: Session, token: str) -> User:
    """The active user behind a token. Looked up on every request, so deactivating
    staff takes effect immediately, a new PIN or role signs out their devices, and a
    PIN session only lasts as long as the device it was started on stays approved."""
    payload = _token_payload(token)
    user = db.query(User).filter(User.username == payload["sub"]).first()
    if not user or not user.active or not effective_role(user):
        raise _credentials_exception()
    if effective_role(user) in PIN_ROLE_VALUES:
        if payload.get("sv") != user.session_version:
            raise _credentials_exception()
        device = db.get(StaffDevice, payload["dv"]) if isinstance(payload.get("dv"), int) else None
        if not device or not device.paired:
            raise _credentials_exception()
    return user


def create_pin_session_token(user: User, device: StaffDevice) -> str:
    return create_access_token(subject=user.username, claims={"sv": user.session_version, "dv": device.id})


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    return resolve_user(db, token)


def require_roles(*roles: StaffRole):
    """Dependency: the signed-in user, who must hold one of these roles."""
    allowed = {role.value for role in roles}

    def dependency(user: User = Depends(get_current_user)) -> User:
        if effective_role(user) not in allowed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You don't have access to this")
        return user

    return dependency


require_admin = require_roles(StaffRole.admin)
require_staff = require_roles(StaffRole.admin, StaffRole.waiter, StaffRole.kitchen, StaffRole.bar)


def hash_pin(pin: str) -> str:
    return get_password_hash(pin)


def verify_pin(pin: str, pin_hash: Optional[str]) -> bool:
    return bool(pin_hash) and verify_password(pin, pin_hash)
