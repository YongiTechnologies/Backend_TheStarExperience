from fastapi.testclient import TestClient
import pytest

from app.core import loyverse


@pytest.fixture(autouse=True)
def loyverse_up():
    loyverse.set_simulated_outage(False)
    yield
    loyverse.set_simulated_outage(False)


def admin_headers(client: TestClient) -> dict:
    login = client.post("/api/auth/token", data={"username": "admin", "password": "test-admin-password"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def place_paid_order(client: TestClient, headers: dict, method: str = "momo") -> dict:
    category = client.post("/api/admin/menu/categories", headers=headers, json={"name": f"drinks-{method}"})
    item = client.post(
        "/api/admin/menu/items",
        headers=headers,
        json={"name": "Club Beer", "price": 25, "is_available": True, "category_id": category.json()["id"]},
    )
    order = client.post(
        "/api/orders",
        json={
            "table_id": 1,
            "customer_name": "Kofi Appiah",
            "customer_phone": "0244123456",
            "items": [{"menu_item_id": item.json()["id"], "quantity": 2}],
        },
    ).json()
    access = {"order_id": order["id"], "tracking_token": order["tracking_token"]}
    payment = client.post(
        "/api/payments/initialize",
        json={**access, "method": method, "network": "mtn" if method == "momo" else None},
    ).json()
    verified = client.post("/api/payments/verify", json={**access, "reference": payment["reference"]})
    assert verified.status_code == 200
    return verified.json()


def test_paid_order_becomes_a_loyverse_receipt(client: TestClient):
    headers = admin_headers(client)
    payment = place_paid_order(client, headers)

    assert payment["status"] == "paid"
    assert payment["loyverse_sync_status"] == "synced"
    assert payment["loyverse_receipt_number"] == "1-1001"

    receipts = client.get("/api/admin/loyverse/receipts", headers=headers).json()
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["receipt_number"] == "1-1001"
    assert receipt["receipt_type"] == "SALE"
    assert receipt["total_money"] == 50
    assert receipt["line_items"][0]["item_name"] == "Club Beer"
    assert receipt["line_items"][0]["quantity"] == 2
    assert receipt["payments"][0]["name"] == "Mobile Money"
    assert receipt["payments"][0]["money_amount"] == 50

    order = client.get("/api/admin/orders", headers=headers).json()[0]
    assert order["loyverse_receipt_number"] == "1-1001"
    assert order["loyverse_sync_status"] == "synced"


def test_outage_keeps_payment_and_retry_syncs_it_once(client: TestClient):
    headers = admin_headers(client)
    outage = client.put("/api/admin/loyverse/simulation/outage", headers=headers, json={"enabled": True})
    assert outage.json()["simulated_outage"] is True

    payment = place_paid_order(client, headers, method="card")
    assert payment["status"] == "paid"
    assert payment["loyverse_sync_status"] == "failed"
    assert payment["loyverse_receipt_number"] is None
    assert client.get("/api/admin/loyverse/receipts", headers=headers).json() == []

    # Still down: the retry fails again without touching the payment.
    retry = client.post("/api/admin/loyverse/sync-failed", headers=headers).json()
    assert retry[0]["loyverse_sync_status"] == "failed"
    assert "outage" in retry[0]["loyverse_sync_error"]

    client.put("/api/admin/loyverse/simulation/outage", headers=headers, json={"enabled": False})
    retry = client.post("/api/admin/loyverse/sync-failed", headers=headers).json()
    assert retry == [{
        "order_id": payment["order_id"],
        "loyverse_receipt_number": "1-1001",
        "loyverse_sync_status": "synced",
        "loyverse_sync_error": None,
    }]

    # Already synced: retrying again must not create a second receipt.
    again = client.post(f"/api/admin/loyverse/orders/{payment['order_id']}/sync", headers=headers)
    assert again.json()["loyverse_receipt_number"] == "1-1001"
    assert client.post("/api/admin/loyverse/sync-failed", headers=headers).json() == []
    receipts = client.get("/api/admin/loyverse/receipts", headers=headers).json()
    assert [r["receipt_number"] for r in receipts] == ["1-1001"]
    assert receipts[0]["payments"][0]["name"] == "Card"


def test_unpaid_order_cannot_be_synced(client: TestClient):
    headers = admin_headers(client)
    assert client.post("/api/admin/loyverse/orders/999/sync", headers=headers).status_code == 404
    assert client.get("/api/admin/loyverse/status").status_code == 401
