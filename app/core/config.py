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
