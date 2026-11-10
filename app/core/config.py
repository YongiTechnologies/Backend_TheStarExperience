from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    PROJECT_NAME: str = "The Star Experience Backend"
    DATABASE_URL: str
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    # A full event shift, so bar staff aren't signed out mid-service.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 720
    ADMIN_USERNAME: str = "admin"
    CORS_ORIGINS: str = "http://localhost:3000"
    # Kept for compatibility with existing local configuration. Prefer
    # CORS_ORIGINS for new deployments because it supports multiple origins.
    FRONTEND_URL: str | None = None
    # Web Push (alerts on staff phones while the screen is locked). Leave unset to turn it off.
    # Generate a key pair once with `python -m app.core.push` and keep the private key secret.
    VAPID_PUBLIC_KEY: str | None = None
    VAPID_PRIVATE_KEY: str | None = None
    # Contact the push services (Google, Apple, Mozilla) can reach about this sender.
    VAPID_SUBJECT: str = "mailto:admin@example.com"
    # Guest payments: "demo" records the payment without moving money; "paystack" charges
    # MoMo and cards through Paystack (see app/core/paystack.py).
    PAYMENT_PROVIDER: Literal["demo", "paystack"] = "demo"
    # Paystack dashboard > Settings > API Keys & Webhooks. sk_test_... for testing, sk_live_... for real money.
    PAYSTACK_SECRET_KEY: str | None = None
    PAYSTACK_CURRENCY: str = "GHS"
    # Where Paystack returns the guest after paying; "{order_id}" is filled in.
    # Defaults to FRONTEND_URL + "/order/{order_id}" (the order tracking page).
    PAYSTACK_CALLBACK_URL: str | None = None
    # Paystack requires an email; guests give only a phone, so receipts go to <phone>@this domain.
    PAYSTACK_GUEST_EMAIL_DOMAIN: str = "guests.thestar.republicbar.com"
    # Loyverse POS, where every paid order is recorded as a sales receipt (see app/core/loyverse.py).
    # "live" calls the Loyverse API; "simulated" keeps receipts in this database; "off" skips it.
    LOYVERSE_MODE: Literal["off", "simulated", "live"] = "simulated"
    # Live mode: a personal access token from Back Office > Integrations > Access tokens.
    LOYVERSE_ACCESS_TOKEN: str | None = None
    # Live mode: only needed when the Loyverse account has more than one store.
    LOYVERSE_STORE_ID: str | None = None
    # Live mode: names of the Loyverse payment types guests' payments are recorded under.
    LOYVERSE_MOMO_PAYMENT_TYPE: str = "Mobile Money"
    LOYVERSE_CARD_PAYMENT_TYPE: str = "Card"
    # Live mode: demo payments collect no money, so by default they never become real
    # Loyverse sales. Turn on only for a deliberate end-to-end test (then void the receipt).
    LOYVERSE_SYNC_DEMO_PAYMENTS: bool = False
    # Simulated mode only: start with Loyverse "down" to show payments surviving an outage.
    LOYVERSE_SIMULATE_OUTAGE: bool = False

    @property
    def cors_origins(self) -> list[str]:
        """Comma-separated frontend origins, kept configurable per deployment."""
        origins = [
            origin.strip().rstrip("/")
            for origin in self.CORS_ORIGINS.split(",")
            if origin.strip()
        ]
        if self.FRONTEND_URL:
            frontend_origin = self.FRONTEND_URL.strip().rstrip("/")
            if frontend_origin and frontend_origin not in origins:
                origins.append(frontend_origin)
        return origins

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def use_psycopg2_driver(cls, value: str) -> str:
        """Pin PostgreSQL URLs to psycopg2, the driver in requirements.txt.
        SQLAlchemy 2.1 maps a bare "postgresql://" to psycopg 3, which isn't
        installed, and many hosts hand out "postgres://", which SQLAlchemy
        rejects. URLs that already name a driver are left alone."""
        if isinstance(value, str):
            for scheme in ("postgres://", "postgresql://"):
                if value.startswith(scheme):
                    return "postgresql+psycopg2://" + value[len(scheme):]
        return value
    
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()
