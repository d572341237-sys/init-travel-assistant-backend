from sqlalchemy.orm import Session

from app.db.models import TravelPlanRecord


def create_travel_plan(
    db: Session,
    *,
    thread_id: str,
    user_id: int | None = None,
    user_message: str,
    answer: str,
) -> TravelPlanRecord:
    record = TravelPlanRecord(
        thread_id=thread_id,
        user_id=user_id,
        user_message=user_message,
        answer=answer,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def list_travel_plans(
    db: Session,
    *,
    thread_id: str,
    user_id: int | None = None,
    limit: int = 20,
) -> list[TravelPlanRecord]:
    query = db.query(TravelPlanRecord)
    if user_id is not None:
        query = query.filter(TravelPlanRecord.user_id == user_id)
    else:
        query = query.filter(TravelPlanRecord.thread_id == thread_id)
    return list(
        query
        .order_by(TravelPlanRecord.created_at.desc(), TravelPlanRecord.id.desc())
        .limit(limit)
        .all()
    )
