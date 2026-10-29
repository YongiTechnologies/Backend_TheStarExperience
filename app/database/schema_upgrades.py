"""
Additive schema upgrades for databases created with Base.metadata.create_all().

create_all() creates missing tables but never adds columns to tables that
already exist, so this creates any missing tables and adds columns introduced
after a database was first created. Every step is additive (nullable columns, IF NOT EXISTS indexes),
so older app versions sharing the same database keep working.

On PostgreSQL it also enables row-level security on every app table. Supabase
publishes the public schema through its auto-generated REST API; with RLS on
and no policies, the anon/authenticated roles get nothing, while the app (which
connects as the tables' owner) is unaffected.

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
        "waiter_id": "INTEGER REFERENCES users(id)",
        "assigned_at": "TIMESTAMP WITH TIME ZONE",
        "accepted_at": "TIMESTAMP WITH TIME ZONE",
        "kitchen_status": "VARCHAR(16)",
        "bar_status": "VARCHAR(16)",
        "ready_at": "TIMESTAMP WITH TIME ZONE",
        "served_at": "TIMESTAMP WITH TIME ZONE",
    },
    "order_items": {
        "station": "VARCHAR(16)",
    },
    "payments": {
        "method": "VARCHAR(16)",
        "network": "VARCHAR(16)",
    },
    "restaurant_tables": {
        "waiter_id": "INTEGER REFERENCES users(id)",
    },
    "users": {
        "display_name": "VARCHAR(80)",
        "role": "VARCHAR(16)",
        "pin_hash": "VARCHAR(100)",
        "is_active": "BOOLEAN",
        "shift_status": "VARCHAR(16)",
        "failed_pin_attempts": "INTEGER",
        "locked_until": "TIMESTAMP WITH TIME ZONE",
        "shift_ends_at": "TIMESTAMP WITH TIME ZONE",
    },
}

# table -> index name -> statement. Names match what the models create on fresh databases.
ADDITIVE_INDEXES: dict[str, dict[str, str]] = {
    "orders": {
        "ix_orders_tracking_token": "CREATE UNIQUE INDEX IF NOT EXISTS ix_orders_tracking_token ON orders (tracking_token)",
        "ix_orders_idempotency_key": "CREATE UNIQUE INDEX IF NOT EXISTS ix_orders_idempotency_key ON orders (idempotency_key)",
        "ix_orders_waiter_id": "CREATE INDEX IF NOT EXISTS ix_orders_waiter_id ON orders (waiter_id)",
    },
    "restaurant_tables": {
        "ix_restaurant_tables_waiter_id": "CREATE INDEX IF NOT EXISTS ix_restaurant_tables_waiter_id ON restaurant_tables (waiter_id)",
    },
}

POSTGRES_LOCK_TIMEOUT = "5s"
# Arbitrary constant identifying "schema upgrade" for pg_advisory_xact_lock.
SCHEMA_UPGRADE_LOCK_KEY = 74210001


def _tables_without_rls(engine: Engine) -> list[str]:
    """App tables that still have row-level security switched off (PostgreSQL only)."""
    if engine.dialect.name != "postgresql":
        return []
    import app.models  # noqa: F401  (registers every model on Base.metadata)
    from app.database.connection import Base

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() AND c.relkind = 'r' AND NOT c.relrowsecurity "
                "AND c.relname = ANY(:tables)"
            ),
            {"tables": sorted(Base.metadata.tables)},
        )
        return sorted(row[0] for row in rows)


def _missing_tables(existing: set[str]):
    """Tables defined by the models but absent from the database (e.g. new features)."""
    import app.models  # noqa: F401  (registers every model on Base.metadata)
    from app.database.connection import Base

    return [table for name, table in Base.metadata.tables.items() if name not in existing]


def _pending_changes(engine: Engine):
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    # A database with no tables at all is set up by seed.py, not by startup.
    new_tables = _missing_tables(tables) if tables else []
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
    return new_tables, columns, indexes, _tables_without_rls(engine)


def upgrade_schema(engine: Engine) -> list[str]:
    """Add any missing columns and indexes and enable RLS where it's off. Returns the columns added.
    Raises on failure: serving with a half-upgraded schema would break every order request."""
    new_tables, columns, indexes, rls_tables = _pending_changes(engine)
    if not new_tables and not columns and not indexes and not rls_tables:
        return []

    postgres = engine.dialect.name == "postgresql"
    with engine.begin() as connection:
        if postgres:
            connection.execute(text(f"SET LOCAL lock_timeout = '{POSTGRES_LOCK_TIMEOUT}'"))
            connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SCHEMA_UPGRADE_LOCK_KEY})
        # PostgreSQL supports IF NOT EXISTS here (covers another instance winning the race);
        # SQLite doesn't, but it only ever has one writer.
        if_not_exists = "IF NOT EXISTS " if postgres else ""
        if new_tables:
            from app.database.connection import Base

            Base.metadata.create_all(bind=connection, tables=new_tables, checkfirst=True)
        for table, name, sql_type in columns:
            connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {if_not_exists}{name} {sql_type}"))
        for _table, _name, statement in indexes:
            connection.execute(text(statement))
        # Tables created just now still need RLS.
        for table in sorted(set(rls_tables) | ({t.name for t in new_tables} if postgres else set())):
            connection.execute(text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))

    added = [f"{table}.{name}" for table, name, _ in columns]
    for table in new_tables:
        logger.info("Schema upgrade: created table %s", table.name)
    for column in added:
        logger.info("Schema upgrade: added column %s", column)
    if rls_tables:
        logger.info("Schema upgrade: enabled row-level security on %s", ", ".join(rls_tables))
    return added
