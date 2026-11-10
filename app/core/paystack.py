"""
Paystack (https://paystack.com/docs/api/) for guest payments in Ghana: MoMo and card.

Flow:
1. /api/payments/initialize creates a Paystack transaction and returns its hosted
   checkout page (authorization_url) as `checkout_url`. The guest pays there.
2. Paystack redirects the guest back to the frontend (PAYSTACK_CALLBACK_URL) and
   also calls our webhook. Either the frontend's /api/payments/verify or the webhook
   marks the payment paid, whichever comes first; both ask Paystack to confirm.

The secret key never leaves the backend; the frontend needs no Paystack key.
"""
import hashlib
import hmac
from typing import Optional
from urllib.parse import urlsplit

import httpx

from app.core.config import settings

API_URL = "https://api.paystack.co"
# Tests swap in an httpx.MockTransport.
_transport: httpx.BaseTransport | None = None

CHANNELS = {"momo": ["mobile_money"], "card": ["card"]}


class PaystackError(Exception):
    pass


def to_minor_units(amount: float) -> int:
    """GHS to pesewas (Paystack amounts are in the currency's smallest unit)."""
    return int(round(amount * 100))


def _request(method: str, path: str, **kwargs) -> dict:
    if not settings.PAYSTACK_SECRET_KEY:
        raise PaystackError("PAYSTACK_SECRET_KEY is not set")
    try:
        with httpx.Client(
            base_url=API_URL,
            headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
            timeout=20,
            transport=_transport,
        ) as http:
            response = http.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise PaystackError(f"Couldn't reach Paystack: {exc}") from exc
    try:
        body = response.json()
    except ValueError:
        raise PaystackError(f"Paystack answered {response.status_code}: {response.text[:200]}")
    if response.status_code >= 400 or not body.get("status"):
        raise PaystackError(f"Paystack answered {response.status_code}: {body.get('message') or 'no details'}")
    return body.get("data") or {}


def _is_allowed_frontend(url: str) -> bool:
    """Only return guests to our own frontend, never to a URL a caller made up."""
    parts = urlsplit(url)
    return parts.scheme in {"http", "https"} and f"{parts.scheme}://{parts.netloc}" in settings.cors_origins


def callback_url(order_id: int, requested: Optional[str] = None) -> Optional[str]:
    """Where Paystack sends the guest after paying (it appends ?reference=...)."""
    if requested and _is_allowed_frontend(requested):
        return requested
    if settings.PAYSTACK_CALLBACK_URL:
        return settings.PAYSTACK_CALLBACK_URL.replace("{order_id}", str(order_id))
    if settings.FRONTEND_URL:
        return f"{settings.FRONTEND_URL.rstrip('/')}/order/{order_id}"
    return None  # Paystack falls back to the callback URL set on the dashboard.


def guest_email(phone: str) -> str:
    """Paystack requires an email, but guests only give a phone number."""
    digits = "".join(ch for ch in phone if ch.isdigit()) or "guest"
    return f"{digits}@{settings.PAYSTACK_GUEST_EMAIL_DOMAIN}"


def initialize(*, reference: str, amount: float, email: str, method: str, order_id: int,
               table_number: Optional[str], customer_name: Optional[str],
               requested_callback: Optional[str] = None) -> dict:
    """Create a transaction; returns Paystack's data (authorization_url, access_code, reference)."""
    payload = {
        "reference": reference,
        "amount": to_minor_units(amount),
        "currency": settings.PAYSTACK_CURRENCY,
        "email": email,
        "channels": CHANNELS.get(method, ["mobile_money", "card"]),
        "metadata": {
            "order_id": order_id,
            "table_number": table_number,
            "custom_fields": [
                {"display_name": "Order", "variable_name": "order_id", "value": str(order_id)},
                {"display_name": "Table", "variable_name": "table_number", "value": table_number or ""},
                {"display_name": "Guest", "variable_name": "customer_name", "value": customer_name or ""},
            ],
        },
    }
    url = callback_url(order_id, requested_callback)
    if url:
        payload["callback_url"] = url
    return _request("POST", "/transaction/initialize", json=payload)


def verify(reference: str) -> dict:
    """The transaction as Paystack sees it: data["status"] is "success", "failed",
    "abandoned" (not finished yet), "ongoing", "pending", "reversed", ..."""
    return _request("GET", f"/transaction/verify/{reference}")


def signature_is_valid(raw_body: bytes, signature: Optional[str]) -> bool:
    """Webhooks are signed with HMAC-SHA512 of the raw body, keyed by the secret key."""
    if not signature or not settings.PAYSTACK_SECRET_KEY:
        return False
    expected = hmac.new(settings.PAYSTACK_SECRET_KEY.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature)
