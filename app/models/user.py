import enum
import hashlib
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class StaffRole(str, enum.Enum):
    admin = "admin"
    waiter = "waiter"
    kitchen = "kitchen"
    bar = "bar"


class ShiftStatus(str, enum.Enum):
    on_shift = "on_shift"
    on_break = "on_break"
    off = "off"


# Roles that sign in with a 4-digit PIN from the staff picker. Admins use a password.
PIN_ROLES = (StaffRole.waiter, StaffRole.kitchen, StaffRole.bar)


class User(BaseModel):
    __tablename__ = "users"

    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    # Staff columns were added after launch, so they are nullable in the database;
    # use the properties below rather than reading them raw.
    display_name = Column(String(80), nullable=True)
    role = Column(String(16), nullable=True)  # StaffRole value; plain text so new roles never need a migration
    pin_hash = Column(String(100), nullable=True)
    is_active = Column(Boolean, nullable=True, default=True)
    shift_status = Column(String(16), nullable=True, default=ShiftStatus.off.value)
    failed_pin_attempts = Column(Integer, nullable=True, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    # When the PIN session that started the shift runs out (see `shift`).
    shift_ends_at = Column(DateTime(timezone=True), nullable=True)

    tables = relationship("RestaurantTable", back_populates="waiter")

    @property
    def name(self) -> str:
        return self.display_name or self.username

    @property
    def active(self) -> bool:
        return self.is_active is not False

    @property
    def shift(self) -> str:
        """on_shift / on_break / off. A shift lapses when the PIN session that started it
        expires, so someone who went home without signing out stops receiving orders."""
        status = self.shift_status or ShiftStatus.off.value
        ends = self.shift_ends_at
        if ends is not None and ends.tzinfo is None:
            ends = ends.replace(tzinfo=timezone.utc)  # SQLite returns naive UTC
        if status != ShiftStatus.off.value and ends is not None and ends <= datetime.now(timezone.utc):
            return ShiftStatus.off.value
        return status

    @property
    def session_version(self) -> str:
        """Changes with the role or PIN, which signs out every device using the old ones."""
        return hashlib.sha256(f"{self.role}:{self.pin_hash}".encode()).hexdigest()[:16]
