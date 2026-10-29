import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.routers import admin, auth, health, menu, orders, payments, staff, staff_admin, tables
from sqlalchemy import text

from app.core.events import PostgresListener
from app.core.stations import backfill_station_statuses
from app.database.connection import SessionLocal, engine
from app.database.schema_upgrades import upgrade_schema

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Bring existing databases up to date (additive only) before serving requests.
    # Failing here is deliberate: a deploy that can't upgrade the schema should not
    # go live (the previous version keeps serving) rather than 500 on every order.
    try:
        upgrade_schema(engine)
    except Exception:
        logger.exception("Schema upgrade failed; refusing to start")
        raise
    try:
        with SessionLocal() as db:
            if backfilled := backfill_station_statuses(db):
                logger.info("Gave %d open order(s) kitchen/bar statuses", backfilled)
    except Exception:
        # Not fatal: those orders can still be moved on from the admin board.
        logger.exception("Couldn't backfill station statuses")
    # On PostgreSQL, hear staff events published by any app instance (see app/core/events.py).
    listener = PostgresListener(settings.DATABASE_URL) if engine.dialect.name == "postgresql" else None
    if listener:
        listener.start()
    yield
    if listener:
        listener.stop()


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Backend API for The Star Experience",
    version="1.0.0",
    lifespan=lifespan,
)

# Configure CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    return {"message": "API is running"}


@app.get("/test-db")
def test_database():
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))

    return {"database": "connected" if result.scalar() == 1 else "not connected"}

app.include_router(health.router, prefix="/api")
app.include_router(tables.router, prefix="/api/tables", tags=["tables"])
app.include_router(menu.router, prefix="/api/menu", tags=["menu"])
app.include_router(orders.router, prefix="/api/orders", tags=["orders"])
app.include_router(payments.router, prefix="/api/payments", tags=["payments"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(staff_admin.router, prefix="/api/admin", tags=["staff admin"])
app.include_router(staff.router, prefix="/api/staff", tags=["staff"])
