"""
Loyverse POS: every paid order becomes a sales receipt in Loyverse, so sales,
stock and reports stay in the Back Office the venue already uses.

Two modes (LOYVERSE_MODE):
* "live" calls the Loyverse API (https://developer.loyverse.com/docs/) with a
  personal access token. Each menu item must be linked to its Loyverse item
  variant first (link_menu_items matches them by name).
* "simulated" builds the same receipts but keeps them in
  loyverse_simulated_receipts, with made-up store, item and payment type IDs,
  for demos without a Loyverse account.

Syncing never undoes a payment: if Loyverse is unreachable the payment stays
paid, the failure is recorded on it and an admin can retry it later. Receipts
carry the order name ("STAR-ORD<id>"), and live mode looks that up before
creating one, so a retry after a lost response can't record the sale twice.
"""
import json
import logging
import re
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.loyverse import SimulatedLoyverseReceipt
from app.models.menu import MenuItem
from app.models.order import Order, OrderItem
from app.models.payment import PaymentStatus

logger = logging.getLogger(__name__)

SYNCED = "synced"
FAILED = "failed"
# Demo payment while LOYVERSE_MODE=live: no money was collected, so no real sale is recorded.
SKIPPED = "skipped"
DEMO_SKIP_REASON = "Demo payment: no money was collected, so it is not sent to Loyverse in live mode"
RECEIPT_SOURCE = "The Star Experience"

API_URL = "https://api.loyverse.com/v1.0"
# Tests swap in an httpx.MockTransport.
_transport: httpx.BaseTransport | None = None

# Fixed so simulated IDs stay the same across restarts and databases.
_SIMULATED_NAMESPACE = uuid.UUID("6f1d8c2e-3b7a-4c55-9a0e-5d2f8b1c4e70")
PAYMENT_TYPE_NAMES = {"momo": "Mobile Money", "card": "Card"}

# Simulated mode only; toggled at runtime from the admin API for demos.
_simulated_outage = settings.LOYVERSE_SIMULATE_OUTAGE

# Live mode: store and payment type IDs rarely change, so look them up once.
_live_ids: dict[str, str] = {}


class LoyverseError(Exception):
    pass


def simulated_outage() -> bool:
    return _simulated_outage


def set_simulated_outage(enabled: bool) -> None:
    global _simulated_outage
    _simulated_outage = enabled


def clear_cache() -> None:
    _live_ids.clear()


def order_name(order: Order) -> str:
    return f"STAR-ORD{order.id}"


def payment_type_name(method: str) -> str:
    if method == "card":
        return settings.LOYVERSE_CARD_PAYMENT_TYPE
    return settings.LOYVERSE_MOMO_PAYMENT_TYPE


def _normalize(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().casefold()


def build_receipt(order: Order, client) -> dict:
    """The body for Loyverse's POST /v1.0/receipts."""
    method = order.payment.method or "momo"
    return {
        "store_id": client.store_id(),
        "order": order_name(order),
        "receipt_date": datetime.now(timezone.utc).isoformat(),
        "source": RECEIPT_SOURCE,
        "note": f"Table {order.table_number}" + (f" - {order.notes}" if order.notes else ""),
        # Our prices, so the receipt total matches what the guest paid.
        "line_items": [
            {"variant_id": client.variant_id(item), "quantity": item.quantity, "price": round(item.unit_price, 2)}
            for item in order.items
        ],
        # Loyverse accepts a single payment per receipt created through the API.
        "payments": [{"payment_type_id": client.payment_type_id(method), "money_amount": round(order.payment.amount, 2)}],
    }


class SimulatedLoyverse:
    """Answers like the Loyverse API, keeping receipts in our own database."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _id(name: str) -> str:
        return str(uuid.uuid5(_SIMULATED_NAMESPACE, name))

    def store_id(self) -> str:
        return self._id("store")

    def variant_id(self, item: OrderItem) -> str:
        return self._id(f"menu-item-{item.menu_item_id}")

    def payment_type_id(self, method: str) -> str:
        return self._id(f"payment-type-{method}")

    def create_receipt(self, receipt: dict) -> dict:
        if _simulated_outage:
            raise LoyverseError("Simulated Loyverse outage")

        # Loyverse fills in item names from its catalog; ours is the menu.
        names = {self._id(f"menu-item-{item_id}"): name for item_id, name in self.db.query(MenuItem.id, MenuItem.name)}
        payment_names = {self.payment_type_id(method): name for method, name in PAYMENT_TYPE_NAMES.items()}
        line_items = [
            {**line, "item_name": names.get(line["variant_id"], "Unknown item"),
             "total_money": round(line["price"] * line["quantity"], 2)}
            for line in receipt["line_items"]
        ]
        payments = [
            {**payment, "name": payment_names.get(payment["payment_type_id"], "Other")}
            for payment in receipt["payments"]
        ]
        total = round(sum(line["total_money"] for line in line_items), 2)
        result = {**receipt, "receipt_type": "SALE", "total_money": total, "line_items": line_items, "payments": payments}

        row = SimulatedLoyverseReceipt(
            order_ref=receipt["order"],
            total_money=total,
            payment_type=", ".join(payment["name"] for payment in payments),
            payload="{}",
        )
        self.db.add(row)
        self.db.flush()
        row.receipt_number = f"1-{1000 + row.id}"
        result["receipt_number"] = row.receipt_number
        row.payload = json.dumps(result)
        return result

    def list_receipts(self, limit: int) -> list[dict]:
        rows = (
            self.db.query(SimulatedLoyverseReceipt)
            .order_by(SimulatedLoyverseReceipt.id.desc())
            .limit(limit)
            .all()
        )
        return [{**json.loads(row.payload), "created_at": row.created_at} for row in rows]


class LiveLoyverse:
    """The Loyverse API, authorized with a personal access token from the Back Office."""

    def __init__(self, db: Session):
        if not settings.LOYVERSE_ACCESS_TOKEN:
            raise LoyverseError("LOYVERSE_ACCESS_TOKEN is not set")
        self.db = db

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            with httpx.Client(
                base_url=API_URL,
                headers={"Authorization": f"Bearer {settings.LOYVERSE_ACCESS_TOKEN}"},
                timeout=15,
                transport=_transport,
            ) as http:
                response = http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise LoyverseError(f"Couldn't reach Loyverse: {exc}") from exc
        if response.status_code >= 400:
            try:
                errors = response.json().get("errors") or []
                detail = "; ".join(f"{e.get('code')}: {e.get('details')}" for e in errors)
            except ValueError:
                detail = response.text[:200]
            raise LoyverseError(f"Loyverse answered {response.status_code}: {detail or 'no details'}")
        return response.json()

    def _list(self, path: str, key: str, **params) -> list[dict]:
        """Every page of a list endpoint (250 per page, Loyverse's maximum)."""
        results, cursor = [], None
        while True:
            page = self._request("GET", path, params={**params, "limit": 250, **({"cursor": cursor} if cursor else {})})
            results.extend(page.get(key) or [])
            cursor = page.get("cursor")
            if not cursor:
                return results

    def store_id(self) -> str:
        if settings.LOYVERSE_STORE_ID:
            return settings.LOYVERSE_STORE_ID
        if "store" not in _live_ids:
            stores = self._list("/stores", "stores")
            if len(stores) != 1:
                names = ", ".join(f"{s.get('name')} ({s.get('id')})" for s in stores) or "none"
                raise LoyverseError(f"Set LOYVERSE_STORE_ID to one of these stores: {names}")
            _live_ids["store"] = stores[0]["id"]
        return _live_ids["store"]

    def store_name(self) -> str | None:
        store = self._request("GET", f"/stores/{self.store_id()}")
        return store.get("name")

    def variant_id(self, item: OrderItem) -> str:
        variant = item.menu_item.loyverse_variant_id if item.menu_item else None
        if not variant:
            name = item.menu_item.name if item.menu_item else f"Menu item #{item.menu_item_id}"
            raise LoyverseError(f"'{name}' isn't linked to a Loyverse item yet")
        return variant

    def payment_type_id(self, method: str) -> str:
        key = f"payment-type-{method}"
        if key not in _live_ids:
            wanted = payment_type_name(method)
            types = self._list("/payment_types", "payment_types")
            match = next((t for t in types if _normalize(t.get("name") or "") == _normalize(wanted)), None)
            if not match:
                found = ", ".join(t.get("name") or "?" for t in types) or "none"
                raise LoyverseError(f"No Loyverse payment type named '{wanted}' (found: {found})")
            _live_ids[key] = match["id"]
        return _live_ids[key]

    def create_receipt(self, receipt: dict) -> dict:
        existing = self._request(
            "GET", "/receipts", params={"order": receipt["order"], "store_id": receipt["store_id"], "limit": 1}
        ).get("receipts")
        if existing:
            logger.info("Order %s already has Loyverse receipt %s", receipt["order"], existing[0]["receipt_number"])
            return existing[0]
        return self._request("POST", "/receipts", json=receipt)

    def list_receipts(self, limit: int) -> list[dict]:
        page = self._request(
            "GET", "/receipts", params={"store_id": self.store_id(), "source": RECEIPT_SOURCE, "limit": min(limit, 250)}
        )
        return page.get("receipts") or []

    def items(self) -> list[dict]:
        return self._list("/items", "items")

    def get_variant(self, variant_id: str) -> dict:
        return self._request("GET", f"/variants/{variant_id}")


def get_client(db: Session):
    if settings.LOYVERSE_MODE == "simulated":
        return SimulatedLoyverse(db)
    if settings.LOYVERSE_MODE == "live":
        return LiveLoyverse(db)
    raise LoyverseError("Loyverse is turned off (LOYVERSE_MODE=off)")


def sync_order_receipt(db: Session, order: Order) -> None:
    """Record a paid order as a Loyverse receipt, at most once. Never raises:
    a failure is stored on the payment for an admin to retry. Caller commits."""
    payment = order.payment
    if settings.LOYVERSE_MODE == "off" or not payment or payment.status != PaymentStatus.paid:
        return
    if payment.loyverse_receipt_number:
        return
    if settings.LOYVERSE_MODE == "live" and payment.provider == "demo" and not settings.LOYVERSE_SYNC_DEMO_PAYMENTS:
        payment.loyverse_sync_status = SKIPPED
        payment.loyverse_sync_error = DEMO_SKIP_REASON
        return
    try:
        # Savepoint, so a half-written simulated receipt can't ride along with the commit.
        with db.begin_nested():
            client = get_client(db)
            result = client.create_receipt(build_receipt(order, client))
    except Exception as exc:
        logger.warning("Couldn't send order %s to Loyverse: %s", order.id, exc)
        payment.loyverse_sync_status = FAILED
        payment.loyverse_sync_error = str(exc)[:500]
        return
    payment.loyverse_receipt_number = result["receipt_number"]
    payment.loyverse_sync_status = SYNCED
    payment.loyverse_sync_error = None


def link_menu_items(db: Session, client: LiveLoyverse, overwrite: bool = False) -> dict:
    """Link menu items to Loyverse item variants with the same name (case and spacing
    ignored). Items with several variants (sizes etc.) must be linked by hand.
    Caller commits."""
    by_name: dict[str, list[dict]] = {}
    for item in client.items():
        by_name.setdefault(_normalize(item.get("item_name") or ""), []).append(item)

    report = {"linked": [], "already_linked": [], "not_in_loyverse": [], "needs_manual_link": [],
              "price_differences": [], "not_on_menu": []}
    menu_names = set()
    for menu_item in db.query(MenuItem).order_by(MenuItem.name):
        key = _normalize(menu_item.name)
        menu_names.add(key)
        if menu_item.loyverse_variant_id and not overwrite:
            report["already_linked"].append(menu_item.name)
            continue
        matches = by_name.get(key, [])
        variants = [variant for item in matches for variant in item.get("variants") or []]
        if not variants:
            report["not_in_loyverse"].append(menu_item.name)
            continue
        if len(variants) > 1:
            report["needs_manual_link"].append({
                "menu_item_id": menu_item.id,
                "name": menu_item.name,
                "variants": [_variant_summary(matches, variant) for variant in variants],
            })
            continue
        variant = variants[0]
        menu_item.loyverse_variant_id = variant["variant_id"]
        report["linked"].append(menu_item.name)
        price = variant.get("default_price")
        if price is not None and round(price, 2) != round(menu_item.price, 2):
            report["price_differences"].append(
                {"name": menu_item.name, "app_price": menu_item.price, "loyverse_price": price}
            )
    report["not_on_menu"] = sorted(
        item.get("item_name") for name, items in by_name.items() if name not in menu_names for item in items
    )
    return report


def _variant_summary(items: list[dict], variant: dict) -> dict:
    item = next((i for i in items if i.get("id") == variant.get("item_id")), items[0])
    options = [variant.get(f"option{n}_value") for n in (1, 2, 3) if variant.get(f"option{n}_value")]
    return {
        "variant_id": variant["variant_id"],
        "name": " / ".join([item.get("item_name") or "", *options]),
        "price": variant.get("default_price"),
    }
