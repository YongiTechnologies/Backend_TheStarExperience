from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models.base import BaseModel


class StaffDevice(BaseModel):
    """A phone or tablet an admin has approved for staff sign-in.

    The staff list and PIN sign-in only answer approved devices, so nobody outside
    can see staff names, guess PINs or lock staff out. An admin creates the device
    with a one-time setup code; entering the code on the device swaps it for a
    long-lived device token (only its hash is stored). Deleting the row revokes it.
    """

    __tablename__ = "staff_devices"

    name = Column(String(60), nullable=False)
    token_hash = Column(String(64), nullable=True, unique=True, index=True)
    pairing_code_hash = Column(String(64), nullable=True, index=True)
    pairing_expires_at = Column(DateTime(timezone=True), nullable=True)
    created_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    created_by = relationship("User")

    @property
    def paired(self) -> bool:
        return self.token_hash is not None


class PushSubscription(BaseModel):
    """A browser's Web Push subscription for a signed-in staff member, so their phone
    can alert them about new and ready orders while the screen is locked."""

    __tablename__ = "push_subscriptions"

    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    device_id = Column(Integer, ForeignKey("staff_devices.id"), nullable=True, index=True)
    endpoint = Column(Text, nullable=False, unique=True)
    p256dh = Column(String(200), nullable=False)
    auth = Column(String(100), nullable=False)
