from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.security import get_password_hash
from app.database.connection import Base, get_db
from app.main import app
from app.models import MenuCategory, RestaurantTable, User


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    db = TestingSession()
    db.add_all([
        User(username="admin", hashed_password=get_password_hash("test-admin-password")),
        RestaurantTable(table_number="12", capacity=4),
    ])
    db.commit()
    db.close()

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


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
    assert client.get(f"/api/orders/{order.json()['id']}").status_code == 200

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
