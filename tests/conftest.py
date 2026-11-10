from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.security import get_password_hash
from app.database.connection import Base, get_db
from app.main import app
from app.models import MenuCategory, RestaurantTable, User


@pytest.fixture(autouse=True)
def isolated_loyverse(monkeypatch):
    """Never let tests reach the real Loyverse or Paystack accounts, whatever the local .env says.
    Tests that exercise live mode switch it on themselves against a mocked API."""
    monkeypatch.setattr(settings, "LOYVERSE_MODE", "simulated")
    monkeypatch.setattr(settings, "LOYVERSE_ACCESS_TOKEN", None)
    monkeypatch.setattr(settings, "LOYVERSE_STORE_ID", None)
    monkeypatch.setattr(settings, "LOYVERSE_MOMO_PAYMENT_TYPE", "Mobile Money")
    monkeypatch.setattr(settings, "LOYVERSE_CARD_PAYMENT_TYPE", "Card")
    monkeypatch.setattr(settings, "LOYVERSE_SIMULATE_OUTAGE", False)
    monkeypatch.setattr(settings, "LOYVERSE_SYNC_DEMO_PAYMENTS", False)
    monkeypatch.setattr(settings, "PAYMENT_PROVIDER", "demo")
    monkeypatch.setattr(settings, "PAYSTACK_SECRET_KEY", None)
    monkeypatch.setattr(settings, "PAYSTACK_CALLBACK_URL", None)


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
