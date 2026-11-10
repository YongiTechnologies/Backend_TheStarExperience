from fastapi.testclient import TestClient


def test_admin_menu_and_order_workflow(client: TestClient):
    login = client.post(
        "/api/auth/token",
        data={"username": "admin", "password": "test-admin-password"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    category = client.post(
        "/api/admin/menu/categories",
        headers=headers,
        json={"name": "beer", "description": "Cold beer"},
    )
    assert category.status_code == 201

    item = client.post(
        "/api/admin/menu/items",
        headers=headers,
        json={
            "name": "Club Beer",
            "description": "Cold",
            "price": 25,
            "is_available": True,
            "category_id": category.json()["id"],
        },
    )
    assert item.status_code == 201
    assert client.get("/api/admin/menu/items", headers=headers).json()[0]["name"] == "Club Beer"

    order = client.post(
        "/api/orders",
        json={
            "table_id": 1,
            "customer_name": "Kofi Appiah",
            "customer_phone": "0244123456",
            "items": [{"menu_item_id": item.json()["id"], "quantity": 2}],
        },
    )
    assert order.status_code == 201
    order_url = f"/api/orders/{order.json()['id']}"
    assert client.get(order_url).status_code == 404  # tracking token required
    tracked = client.get(order_url, headers={"X-Order-Token": order.json()["tracking_token"]})
    assert tracked.status_code == 200

    admin_orders = client.get("/api/admin/orders", headers=headers)
    assert admin_orders.status_code == 200
    assert admin_orders.json()[0]["status"] == "received"
    assert admin_orders.json()[0]["items"][0]["id"] == "1"

    updated = client.patch(
        f"/api/admin/orders/{order.json()['id']}/status",
        headers=headers,
        json={"status": "preparing"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "preparing"


def test_login_without_role_is_refused_up_front(client: TestClient):
    from app.core.security import get_password_hash
    from app.database.connection import get_db
    from app.main import app
    from app.models import User

    session = next(app.dependency_overrides[get_db]())
    session.add(User(username="owner@example.com", hashed_password=get_password_hash("pw-12345678")))
    session.commit()
    session.close()

    login = client.post("/api/auth/token", data={"username": "owner@example.com", "password": "pw-12345678"})
    assert login.status_code == 403
    assert "no role" in login.json()["detail"]
