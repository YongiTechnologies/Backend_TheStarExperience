"""
Live updates for staff screens.

Endpoints call emit(db, ...) while changing data; the events are published only
once that transaction commits (and dropped on rollback), so screens never hear
about changes that didn't happen.

On PostgreSQL, publishing uses NOTIFY and every app instance LISTENs, so staff
connected to any instance (e.g. local + hosted) all get the event. On SQLite
(local tests) events are delivered within the process.

Events are small hints ({"type": "order.updated", "order_id": 12, ...}); screens
re-fetch what they show, so they never apply stale or partial data.
"""
import asyncio
import json
import logging
import select
import threading
from typing import Optional

from sqlalchemy import event as sa_event
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

CHANNEL = "star_events"


class EventBus:
    """Fans events out to the asyncio queues of connected SSE clients (thread-safe)."""

    def __init__(self) -> None:
        self._subscribers: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._lock = threading.Lock()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subscribers.add((asyncio.get_running_loop(), queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers = {(loop, q) for loop, q in self._subscribers if q is not queue}

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def dispatch(self, payload: str) -> None:
        with self._lock:
            targets = list(self._subscribers)
        for loop, queue in targets:
            try:
                loop.call_soon_threadsafe(_offer, queue, payload)
            except RuntimeError:
                # That client's event loop is gone; it will be unsubscribed when its stream ends.
                pass


def _offer(queue: asyncio.Queue, payload: str) -> None:
    if queue.full():
        # A slow client only needs the latest hints: drop the oldest.
        queue.get_nowait()
    queue.put_nowait(payload)


bus = EventBus()


def emit(db: Session, event_type: str, **data) -> None:
    """Queue an event to publish when db's current transaction commits."""
    db.info.setdefault("pending_events", []).append(json.dumps({"type": event_type, **data}, default=str))


@sa_event.listens_for(Session, "after_commit")
def _publish_after_commit(session: Session) -> None:
    events = session.info.pop("pending_events", None)
    if events:
        publish(events)


@sa_event.listens_for(Session, "after_rollback")
def _drop_after_rollback(session: Session) -> None:
    session.info.pop("pending_events", None)


def publish(payloads: list[str]) -> None:
    from app.database.connection import engine

    if engine.dialect.name == "postgresql":
        try:
            with engine.connect() as connection:
                for payload in payloads:
                    connection.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": payload})
                connection.commit()
            return
        except Exception:
            logger.exception("NOTIFY failed; delivering events to this instance only")
    for payload in payloads:
        bus.dispatch(payload)


class PostgresListener(threading.Thread):
    """LISTENs on the events channel and hands notifications to the in-process bus."""

    def __init__(self, database_url: str) -> None:
        super().__init__(name="star-events-listener", daemon=True)
        # psycopg2 wants a plain libpq URL (no "+psycopg2" driver suffix).
        self._dsn = make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
        self._stopping = threading.Event()

    def stop(self) -> None:
        self._stopping.set()

    def run(self) -> None:
        import psycopg2
        import psycopg2.extensions

        backoff = 1.0
        while not self._stopping.is_set():
            connection: Optional[psycopg2.extensions.connection] = None
            try:
                connection = psycopg2.connect(self._dsn, connect_timeout=10)
                connection.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
                connection.cursor().execute(f"LISTEN {CHANNEL}")
                backoff = 1.0
                while not self._stopping.is_set():
                    if select.select([connection], [], [], 5) == ([], [], []):
                        continue
                    connection.poll()
                    while connection.notifies:
                        bus.dispatch(connection.notifies.pop(0).payload)
            except Exception:
                logger.exception("Event listener lost its connection; retrying in %.0fs", backoff)
                self._stopping.wait(backoff)
                backoff = min(backoff * 2, 30)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass
