import json
from typing import Any

from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.db.models import SessionStateRecord


def get_session_state(thread_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        record = db.get(SessionStateRecord, thread_id)
        if not record:
            return {}
        return {
            "pending_attraction_choices": _loads(record.pending_attraction_choices),
            "last_route_context": _loads(record.last_route_context),
        }


def upsert_session_state(
    thread_id: str,
    *,
    pending_attraction_choices: dict[str, Any] | None | object = ...,
    last_route_context: dict[str, Any] | None | object = ...,
) -> None:
    with SessionLocal() as db:
        record = db.get(SessionStateRecord, thread_id)
        if not record:
            record = SessionStateRecord(thread_id=thread_id)
            db.add(record)

        if pending_attraction_choices is not ...:
            record.pending_attraction_choices = _dumps(pending_attraction_choices)
        if last_route_context is not ...:
            record.last_route_context = _dumps(last_route_context)

        db.commit()


def delete_session_state(thread_id: str) -> None:
    with SessionLocal() as db:
        record = db.get(SessionStateRecord, thread_id)
        if record:
            db.delete(record)
            db.commit()


def _dumps(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _loads(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None
