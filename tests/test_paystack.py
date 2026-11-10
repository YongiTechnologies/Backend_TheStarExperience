"""Paystack payments against a fake Paystack API (httpx.MockTransport): no real calls."""
import hashlib
import hmac
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from app.core import paystack
from app.core.config import settings

SECRET = "sk_test_fake"


class FakePaystack:
    def __init__(self):
        self.transactions: dict[str, dict] = {}
        self.initialized: list[dict] = []

    def pay(self, reference: str, amount: int | None = None, channel: str = "mobile_money"):
        tx = self.transactions[reference]
        tx.update(status="success", channel=channel, amount=amount if amount is not None else tx["amount"])

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {SECRET}"
        path = request.url.path
        if path == "/transaction/initialize":
            body = json.loads(request.content)
            self.initialized.append(body)
            self.transactions[body["reference"]] = {
                "reference": body["reference"], "amount": body["amount"], "currency": body["currency"],
                "status": "abandoned", "channel": None, "metadata": body["metadata"],
            }
            return httpx.Response(200, json={"status": True, "message": "Authorization URL created", "data": {
                "authorization_url": f"https://checkout.paystack.com/{body['reference']}",
                "access_code": "ac", "reference": body["reference"],
            }})
        if path.startswith("/transaction/verify/"):
            tx = self.transactions.get(path.rsplit("/", 1)[1])
            if not tx:
                return httpx.Response(400, json={"status": False, "message": "Transaction reference not found"})
            return httpx.Response(200, json={"status": True, "message": "Verification successful", "data": tx})
        return httpx.Response(404, json={"status": False, "message": path})


@pytest.fixture()
def fake(monkeypatch):
    fake = FakePaystack()
    monkeypatch.setattr(settings, "PAYMENT_PROVIDER", "paystack")
    monkeypatch.setattr(settings, "PAYSTACK_SECRET_KEY", SECRET)
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://star.example")
    monkeypatch.setattr(settings, "CORS_ORIGINS", "https://star.example")
    monkeypatch.setattr(paystack, "_transport", httpx.MockTransport(fake.handler))
    return fake


def admin_headers(client: TestClient) -> dict:
    login = client.post("/api/auth/token", data={"username": "admin", "password": "test-admin-password"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def place_order(client: TestClient) -> dict:
    headers = admin_headers(client)
    category = client.post("/api/admin/menu/categories", headers=headers, json={"name": "drinks"})
    item = client.post("/api/admin/menu/items", headers=headers, json={
        "name": "Club Beer", "price": 25.5, "is_available": True, "category_id": category.json()["id"],
    })
    order = client.post("/api/orders", json={
        "table_id": 1, "customer_name": "Kofi Appiah", "customer_phone": "024 412 3456",
        "items": [{"menu_item_id": item.json()["id"], "quantity": 2}],
    }).json()
    return {"order_id": order["id"], "tracking_token": order["tracking_token"]}


def webhook(client: TestClient, body: dict, secret: str = SECRET):
    raw = json.dumps(body).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()
    return client.post("/api/payments/paystack/webhook", content=raw,
                       headers={"x-paystack-signature": signature, "content-type": "application/json"})


def test_checkout_then_verify_marks_paid(client: TestClient, fake: FakePaystack):
    access = place_order(client)
    # The client's provider choice is ignored: a guest can't ask for "demo" to skip paying.
    started = client.post("/api/payments/initialize",
                          json={**access, "method": "momo", "network": "mtn", "provider": "demo"}).json()
    assert started["provider"] == "paystack"
    assert started["status"] == "pending"
    assert started["checkout_url"] == f"https://checkout.paystack.com/{started['reference']}"
    assert started["access_code"] == "ac"

    sent = fake.initialized[0]
    assert sent["amount"] == 5100  # 2 x GHS 25.50 in pesewas
    assert sent["currency"] == "GHS"
    assert sent["channels"] == ["mobile_money"]
    assert sent["email"] == "0244123456@guests.thestar.republicbar.com"
    assert sent["callback_url"] == f"https://star.example/order/{access['order_id']}"

    verify = {**access, "reference": started["reference"]}
    assert client.post("/api/payments/verify", json=verify).json()["status"] == "pending"

    fake.pay(started["reference"], channel="card")
    paid = client.post("/api/payments/verify", json=verify).json()
    assert paid["status"] == "paid"
    assert paid["method"] == "card"
    assert paid["loyverse_sync_status"] == "synced"  # simulated Loyverse in tests


def test_webhook_records_payment_when_guest_never_returns(client: TestClient, fake: FakePaystack):
    access = place_order(client)
    started = client.post("/api/payments/initialize", json={**access, "method": "momo", "network": "mtn"}).json()
    fake.pay(started["reference"])
    event = {"event": "charge.success", "data": {"reference": started["reference"],
                                                 "metadata": {"order_id": access["order_id"]}}}

    assert webhook(client, event, secret="wrong").status_code == 401
    assert webhook(client, event).status_code == 200
    payment = client.get(f"/api/payments/order/{access['order_id']}",
                         headers={"X-Order-Token": access["tracking_token"]}).json()
    assert payment["status"] == "paid"

    # Paystack may deliver the same event twice: still one Loyverse receipt.
    assert webhook(client, event).status_code == 200
    assert len(client.get("/api/admin/loyverse/receipts", headers=admin_headers(client)).json()) == 1


def test_wrong_amount_is_not_accepted(client: TestClient, fake: FakePaystack):
    access = place_order(client)
    started = client.post("/api/payments/initialize", json={**access, "method": "card"}).json()
    fake.pay(started["reference"], amount=100)
    result = client.post("/api/payments/verify", json={**access, "reference": started["reference"]}).json()
    assert result["status"] == "failed"


def test_retry_finds_earlier_checkout_already_paid(client: TestClient, fake: FakePaystack):
    access = place_order(client)
    first = client.post("/api/payments/initialize", json={**access, "method": "momo", "network": "mtn"}).json()
    fake.pay(first["reference"])
    again = client.post("/api/payments/initialize", json={**access, "method": "momo", "network": "mtn"}).json()
    assert again["status"] == "paid"
    assert len(fake.initialized) == 1


def test_paystack_down_is_a_clear_error(client: TestClient, fake: FakePaystack, monkeypatch):
    monkeypatch.setattr(paystack, "_transport", httpx.MockTransport(lambda request: httpx.Response(500, text="down")))
    access = place_order(client)
    response = client.post("/api/payments/initialize", json={**access, "method": "card"})
    assert response.status_code == 502
    assert client.get(f"/api/payments/order/{access['order_id']}",
                      headers={"X-Order-Token": access["tracking_token"]}).status_code == 404


def test_config_reports_provider(client: TestClient, fake: FakePaystack):
    assert client.get("/api/payments/config").json() == {"provider": "paystack"}


def test_callback_url_only_to_our_frontend(client: TestClient, fake: FakePaystack):
    access = place_order(client)
    ours = f"https://star.example/order/{access['order_id']}?from=popup"
    client.post("/api/payments/initialize", json={**access, "method": "card", "callback_url": ours})
    client.post("/api/payments/initialize", json={**access, "method": "card", "callback_url": "https://evil.example/x"})
    assert fake.initialized[0]["callback_url"] == ours
    assert fake.initialized[1]["callback_url"] == f"https://star.example/order/{access['order_id']}"
