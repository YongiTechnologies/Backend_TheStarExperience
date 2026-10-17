"""
Additive schema upgrades for databases created with Base.metadata.create_all().

create_all() creates missing tables but never adds columns to tables that
already exist, so columns introduced after a database was first created are
added here. Every step is additive (nullable columns, IF NOT EXISTS indexes),
so older app versions sharing the same database keep working.

It runs at startup and takes no locks when the schema is already current. On
PostgreSQL it uses a short lock timeout (so it can't queue behind a long
transaction and stall every other query on the table) and an advisory lock
(so several instances starting together don't race).
"""
import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# table -> column -> SQL type. Columns must be nullable so existing rows stay valid.
ADDITIVE_COLUMNS: dict[str, dict[str, str]] = {
    "orders": {
        "notes": "TEXT",
        "tracking_token": "VARCHAR(64)",
        "idempotency_key": "VARCHAR(64)",
    },
    "payments": {
        "method": "VARCHAR(16)",
        "network": "VARCHAR(16)",
    },
}

# table -> index name -> statement. Names match what the models create on fresh databases.
ADDITIVE_INDEXES: dict[str, dict[str, str]] = {
    "orders": {
        "ix_orders_tracking_token": "CREATE UNIQUE INDEX IF NOT EXISTS ix_orders_tracking_token ON orders (tracking_token)",
        "ix_orders_idempotency_key": "CREATE UNIQUE INDEX IF NOT EXISTS ix_orders_idempotency_key ON orders (idempotency_key)",
    },
}

POSTGRES_LOCK_TIMEOUT = "5s"
# Arbitrary constant identifying "schema upgrade" for pg_advisory_xact_lock.
SCHEMA_UPGRADE_LOCK_KEY = 74210001


def _pending_changes(engine: Engine):
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    columns = [
        (table, name, sql_type)
        for table, wanted in ADDITIVE_COLUMNS.items()
        if table in tables
        for present in [{column["name"] for column in inspector.get_columns(table)}]
        for name, sql_type in wanted.items()
        if name not in present
    ]
    indexes = [
        (table, name, statement)
        for table, wanted in ADDITIVE_INDEXES.items()
        if table in tables
        for present in [{index["name"] for index in inspector.get_indexes(table)}]
        for name, statement in wanted.items()
        if name not in present
    ]
    return columns, indexes


def upgrade_schema(engine: Engine) -> list[str]:
    """Add any missing columns and indexes. Returns the columns that were added.
    Raises on failure: serving with a half-upgraded schema would break every order request."""
    columns, indexes = _pending_changes(engine)
    if not columns and not indexes:
        return []

    postgres = engine.dialect.name == "postgresql"
    with engine.begin() as connection:
        if postgres:
            connection.execute(text(f"SET LOCAL lock_timeout = '{POSTGRES_LOCK_TIMEOUT}'"))
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SCHEMA_UPGRADE_LOCK_KEY})
        # PostgreSQL supports IF NOT EXISTS here (covers another instance winning the race);
        # SQLite doesn't, but it only ever has one writer.
        if_not_exists = "IF NOT EXISTS " if postgres else ""
        for table, name, sql_type in columns:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {if_not_exists}{name} {sql_type}"))
        for _table, _name, statement in indexes:
            connection.execute(text(statement))

    added = [f"{table}.{name}" for table, name, _ in columns]
    for column in added:
        logger.info("Schema upgrade: added column %s", column)
    return added
