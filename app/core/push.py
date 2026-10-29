"""
Web Push to staff phones, so a waiter hears about new orders and orders ready to
serve even with the screen locked (a web page can't make a sound then).

Notifications are queued during the request and sent only after the transaction
commits (nothing is announced for work that was rolled back), on a small thread
pool so a slow push service never delays staff. Subscriptions the push service
reports as gone (404/410) are deleted.

Generate the VAPID key pair once with `python -m app.core.push` and put it in .env.
"""
import base64
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.device import PushSubscription

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="webpush")
# A new-order alert that couldn't be delivered within 15 minutes isn't worth showing.
PUSH_TTL_SECONDS = 15 * 60
_PENDING = "pending_pushes"


def push_enabled() -> bool:
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def notify(db: Session, user_id: Optional[int], title: str, body: str, tag: str, url: str = "/staff/waiter") -> None:
    """Queue a notification to every device this person enabled alerts on; sent after commit.
    The tag groups notifications per order, so "ready" replaces "new" on the lock screen."""
    if not user_id or not push_enabled():
        return
    targets = [
        (sub.id, sub.endpoint, sub.p256dh, sub.auth)
        for sub in db.query(PushSubscription).filter(PushSubscription.user_id == user_id)
    ]
    if targets:
        payload = json.dumps({"title": title, "body": body, "tag": tag, "url": url})
        db.info.setdefault(_PENDING, []).extend((target, payload) for target in targets)


def order_summary(order, lines: Optional[list[tuple[int, str]]] = None) -> str:
    """"2× Kokoroko, 1× Jollof Rice", kept short enough for a lock screen."""
    lines = lines if lines is not None else [
        (item.quantity, item.menu_item.name if item.menu_item else "item") for item in order.items
    ]
    text = ", ".join(f"{quantity}× {name}" for quantity, name in lines)
    return text if len(text) <= 110 else text[:107] + "…"


def notify_order_for_waiter(db: Session, order, waiter_id: Optional[int], *, moved: bool = False, lines=None) -> None:
    title = f"Table {order.table_number}: {'order passed to you' if moved else 'new order'}"
    notify(db, waiter_id, title, f"{order_summary(order, lines)}. Tap to accept.", tag=f"order-{order.id}")


def notify_ready(db: Session, order) -> None:
    stations = " & ".join(name for name, state in (("kitchen", order.kitchen_status), ("bar", order.bar_status)) if state)
    notify(
        db, order.waiter_id, f"Table {order.table_number}: ready to serve",
        f"Order #{order.id}. Pick up from the {stations or 'pass'}.", tag=f"order-{order.id}",
    )


def forget_subscriptions(db: Session, *, user_id: Optional[int] = None, device_id: Optional[int] = None) -> None:
    """Stop alerts to devices that are being signed out (new PIN or role, deactivation, revoked device)."""
    query = db.query(PushSubscription)
    if user_id is not None:
        query = query.filter(PushSubscription.user_id == user_id)
    if device_id is not None:
        query = query.filter(PushSubscription.device_id == device_id)
    query.delete(synchronize_session=False)


def send(endpoint: str, p256dh: str, auth: str, payload: str) -> None:
    from pywebpush import webpush

    webpush(
        {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
        data=payload,
        vapid_private_key=settings.VAPID_PRIVATE_KEY,
        vapid_claims={"sub": settings.VAPID_SUBJECT},  # fresh dict: pywebpush adds aud/exp to it
        ttl=PUSH_TTL_SECONDS,
        timeout=10,
        headers={"Urgency": "high"},  # deliver now even if the phone is dozing
    )


def _deliver(bind, pending) -> None:
    from pywebpush import WebPushException

    gone = []
    for (sub_id, endpoint, p256dh, auth), payload in pending:
        try:
            send(endpoint, p256dh, auth, payload)
        except WebPushException as exc:
            code = exc.response.status_code if exc.response is not None else None
            if code in (404, 410):
                gone.append(sub_id)
            else:
                logger.warning("Push to subscription %s failed (%s)", sub_id, code or exc)
        except Exception:
            logger.exception("Push to subscription %s failed", sub_id)
    if gone:
        with Session(bind=bind) as db:
            db.query(PushSubscription).filter(PushSubscription.id.in_(gone)).delete(synchronize_session=False)
            db.commit()


@sa_event.listens_for(Session, "after_commit")
def _send_after_commit(session: Session) -> None:
    pending = session.info.pop(_PENDING, None)
    if pending:
        _executor.submit(_deliver, session.get_bind(), pending)


@sa_event.listens_for(Session, "after_rollback")
def _drop_after_rollback(session: Session) -> None:
    session.info.pop(_PENDING, None)


def generate_vapid_keys() -> tuple[str, str]:
    """(public, private) as base64url: the public key is what browsers subscribe with."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    return b64(public), b64(private)


if __name__ == "__main__":
    public_key, private_key = generate_vapid_keys()
    print(f"VAPID_PUBLIC_KEY={public_key}\nVAPID_PRIVATE_KEY={private_key}")
