"""Record staff activity (assignments, accepts, station updates, serving) for the audit trail."""
import json
from typing import Optional

from sqlalchemy.orm import Session

from app.models.activity import ActivityEvent
from app.models.user import User


def log_activity(
    db: Session,
    action: str,
    actor: Optional[User] = None,
    order_id: Optional[int] = None,
    table_id: Optional[int] = None,
    **detail,
) -> None:
    """Add an audit row to the current transaction (saved when the caller commits)."""
    db.add(
        ActivityEvent(
            action=action,
            actor_id=actor.id if actor else None,
            order_id=order_id,
            table_id=table_id,
            detail=json.dumps(detail, default=str) if detail else None,
        )
    )
