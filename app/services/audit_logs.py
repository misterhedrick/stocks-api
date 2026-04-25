from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AuditLog


def record_audit_log(
    db: Session,
    *,
    event_type: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    message: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditLog:
    audit_log = AuditLog(
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        message=message,
        payload=payload or {},
    )
    db.add(audit_log)
    return audit_log
