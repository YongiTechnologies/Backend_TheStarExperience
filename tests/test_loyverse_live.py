"""Live Loyverse mode against a fake Loyverse API (httpx.MockTransport): no real calls."""
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from app.core import loyverse
from app.core.config import settings

STORE = "store-1"
BEER_VARIANT = "variant-beer"
FANTA_SMALL, FANTA_LARGE = "variant-fanta-small", "variant-fanta-large"


class FakeLoyverse:
    def __init__(self):
        self.receipts: list[dict] = []
        self.posted: list[dict] = []
        self.items = [
            {"id": "item-beer", "item_name": "club  BEER",
             "variants": [{"variant_id": BEER_VARIANT, "item_id": "item-beer", "default_price": 30}]},
            {"id": "item-fanta", "item_name": "Fanta", "variants": [
                {"variant_id": FANTA_SMALL, "item_id": "item-fanta", "option1_value": "Small", "default_price": 10},
                {"variant_id": FANTA_LARGE, "item_id": "item-fanta", "option1_value": "Large", "default_price": 15},
            ]},
            {"id": "item-water", "item_name": "Voltic Water",
             "variants": [{"variant_id": "variant-water", "item_id": "item-water", "default_price": 5}]},
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer test-token"
        path, params = request.url.path.removeprefix("/v1.0"), request.url.params
        if path == "/stores":
            return httpx.Response(200, json={"stores": [{"id": STORE, "name": "The Star"}]})
        if path == f"/stores/{STORE}":
            return httpx.Response(200, json={"id": STORE, "name": "The Star"})
        if path == "/payment_types":
            return httpx.Response(200, json={"payment_types": [
                {"id": "pt-cash", "name": "Cash"}, {"id": "pt-card", "name": "Card"}, {"id": "pt-momo", "name": "Mobile money"},
            ]})
        if path == "/items":
            return httpx.Response(200, json={"items": self.items})
        if path.startswith("/variants/"):
            variant = path.rsplit("/", 1)[1]
            known = {v["variant_id"] for item in self.items for v in item["variants"]}
            if variant not in known:
                return httpx.Response(404, json={"errors": [{"code": "NOT_FOUND", "details": "The resource was not found"}]})
            return httpx.Response(200, json={"variant_id": variant})
        if path == "/receipts" and request.method == "GET":
            found = [r for r in self.receipts if r["order"] == params.get("order")] if "order" in params else self.receipts
            return httpx.Response(200, json={"receipts": found})
        if path == "/receipts" and request.method == "POST":
            body = json.loads(request.content)
            self.posted.append(body)
            receipt = {**body, "receipt_number": f"2-{1001 + len(self.receipts)}", "receipt_type": "SALE",
                       "total_money": sum(line["price"] * line["quantity"] for line in body["line_items"])}
            self.receipts.append(receipt)
            return httpx.Response(200, json=receipt)
        return httpx.Response(404, json={"errors": [{"code": "NOT_FOUND", "details": path}]})


@pytest.fixture()
def fake(monkeypatch):
    fake = FakeLoyverse()
    monkeypatch.setattr(settings, "LOYVERSE_MODE", "live")
    monkeypatch.setattr(settings, "LOYVERSE_ACCESS_TOKEN", "test-token")
    monkeypatch.setattr(settings, "LOYVERSE_STORE_ID", None)
    monkeypatch.setattr(settings, "LOYVERSE_MOMO_PAYMENT_TYPE", "Mobile Money")
    # These tests pay with the demo provider, so allow demo payments through.
    monkeypatch.setattr(settings, "LOYVERSE_SYNC_DEMO_PAYMENTS", True)
    monkeypatch.setattr(loyverse, "_transport", httpx.MockTransport(fake.handler))
    loyverse.clear_cache()
    yield fake
    loyverse.clear_cache()


def admin_headers(client: TestClient) -> dict:
    login = client.post("/api/auth/token", data={"username": "admin", "password": "test-admin-password"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def add_menu_item(client: TestClient, headers: dict, name: str, price: float) -> int:
    categories = client.get("/api/admin/menu/categories", headers=headers).json()
    category_id = categories[0]["id"] if categories else client.post(
        "/api/admin/menu/categories", headers=headers, json={"name": "drinks"}
    ).json()["id"]
    return client.post(
        "/api/admin/menu/items",
        headers=headers,
        json={"name": name, "price": price, "is_available": True, "category_id": category_id},
    ).json()["id"]


def pay_for(client: TestClient, menu_item_id: int, quantity: int = 2) -> dict:
    order = client.post(
        "/api/orders",
        json={
            "table_id": 1,
            "customer_name": "Kofi Appiah",
            "customer_phone": "0244123456",
            "items": [{"menu_item_id": menu_item_id, "quantity": quantity}],
        },
    ).json()
    access = {"order_id": order["id"], "tracking_token": order["tracking_token"]}
    payment = client.post("/api/payments/initialize", json={**access, "method": "momo", "network": "mtn"}).json()
    return client.post("/api/payments/verify", json={**access, "reference": payment["reference"]}).json()


def test_demo_payment_is_not_a_real_sale_in_live_mode(client: TestClient, fake: FakeLoyverse, monkeypatch):
    monkeypatch.setattr(settings, "LOYVERSE_SYNC_DEMO_PAYMENTS", False)
    headers = admin_headers(client)
    beer = add_menu_item(client, headers, "Club Beer", 25)
    client.post("/api/admin/loyverse/items/link", headers=headers)

    payment = pay_for(client, beer)
    assert payment["status"] == "paid"
    assert payment["loyverse_sync_status"] == "skipped"
    assert payment["loyverse_receipt_number"] is None

    # Retrying doesn't send it either.
    retry = client.post("/api/admin/loyverse/sync-failed", headers=headers).json()
    assert retry[0]["loyverse_sync_status"] == "skipped"
    assert fake.posted == []

    order = client.get("/api/admin/orders", headers=headers).json()[0]
    assert order["loyverse_sync_status"] == "skipped"
    assert "Demo payment" in order["loyverse_sync_error"]
    menu_item = client.get("/api/admin/menu/items", headers=headers).json()[0]
    assert menu_item["loyverse_variant_id"] == BEER_VARIANT


def test_status_checks_store_and_payment_types(client: TestClient, fake: FakeLoyverse):
    status = client.get("/api/admin/loyverse/status", headers=admin_headers(client)).json()
    assert status["connected"] is True
    assert status["store_id"] == STORE
    assert status["store_name"] == "The Star"
    assert status["error"] is None


def test_status_reports_missing_payment_type(client: TestClient, fake: FakeLoyverse, monkeypatch):
    monkeypatch.setattr(settings, "LOYVERSE_MOMO_PAYMENT_TYPE", "MoMo")
    status = client.get("/api/admin/loyverse/status", headers=admin_headers(client)).json()
    assert status["connected"] is False
    assert "No Loyverse payment type named 'MoMo'" in status["error"]


def test_link_by_name_then_paid_order_creates_real_receipt(client: TestClient, fake: FakeLoyverse):
    headers = admin_headers(client)
    beer = add_menu_item(client, headers, "Club Beer", 25)
    add_menu_item(client, headers, "Fanta", 10)
    add_menu_item(client, headers, "Kelewele", 20)

    report = client.post("/api/admin/loyverse/items/link", headers=headers).json()
    assert report["linked"] == ["Club Beer"]
    assert report["not_in_loyverse"] == ["Kelewele"]
    assert [v["variant_id"] for v in report["needs_manual_link"][0]["variants"]] == [FANTA_SMALL, FANTA_LARGE]
    assert report["needs_manual_link"][0]["variants"][1]["name"] == "Fanta / Large"
    assert report["price_differences"] == [{"name": "Club Beer", "app_price": 25, "loyverse_price": 30}]
    assert report["not_on_menu"] == ["Voltic Water"]

    payment = pay_for(client, beer)
    assert payment["status"] == "paid"
    assert payment["loyverse_sync_status"] == "synced"
    assert payment["loyverse_receipt_number"] == "2-1001"

    sent = fake.posted[0]
    assert sent["store_id"] == STORE
    assert sent["order"] == f"STAR-ORD{payment['order_id']}"
    assert sent["line_items"] == [{"variant_id": BEER_VARIANT, "quantity": 2, "price": 25}]
    assert sent["payments"] == [{"payment_type_id": "pt-momo", "money_amount": 50}]

    receipts = client.get("/api/admin/loyverse/receipts", headers=headers).json()
    assert [r["receipt_number"] for r in receipts] == ["2-1001"]


def test_unlinked_item_fails_then_manual_link_and_retry_syncs(client: TestClient, fake: FakeLoyverse):
    headers = admin_headers(client)
    fanta = add_menu_item(client, headers, "Fanta", 10)

    payment = pay_for(client, fanta, quantity=1)
    assert payment["status"] == "paid"
    assert payment["loyverse_sync_status"] == "failed"
    assert fake.posted == []

    bad = client.put(f"/api/admin/loyverse/menu-items/{fanta}/link", headers=headers, json={"variant_id": "nope"})
    assert bad.status_code == 502
    linked = client.put(f"/api/admin/loyverse/menu-items/{fanta}/link", headers=headers, json={"variant_id": FANTA_SMALL})
    assert linked.json()["loyverse_variant_id"] == FANTA_SMALL

    retry = client.post("/api/admin/loyverse/sync-failed", headers=headers).json()
    assert retry[0]["loyverse_sync_status"] == "synced"
    assert fake.posted[0]["line_items"][0]["variant_id"] == FANTA_SMALL


def test_retry_reuses_receipt_loyverse_already_has(client: TestClient, fake: FakeLoyverse):
    headers = admin_headers(client)
    beer = add_menu_item(client, headers, "Club Beer", 25)
    client.post("/api/admin/loyverse/items/link", headers=headers)
    # As if an earlier attempt reached Loyverse but its response was lost.
    fake.receipts.append({"receipt_number": "2-0999", "order": "STAR-ORD1", "receipt_type": "SALE",
                          "total_money": 50, "line_items": [], "payments": []})

    payment = pay_for(client, beer)
    assert payment["order_id"] == 1
    assert payment["loyverse_receipt_number"] == "2-0999"
    assert fake.posted == []
