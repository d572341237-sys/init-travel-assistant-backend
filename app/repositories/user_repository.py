import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.db.models import UserProfileRecord, UserRecord


def get_user_by_id(db: Session, user_id: int) -> UserRecord | None:
    return db.get(UserRecord, user_id)


def get_user_by_username(db: Session, username: str) -> UserRecord | None:
    return db.query(UserRecord).filter(UserRecord.username == username).first()


def create_user(db: Session, *, username: str, password: str) -> UserRecord:
    user = UserRecord(username=username, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    profile = UserProfileRecord(user_id=user.id)
    db.add(profile)
    db.commit()
    return user


def authenticate_user(db: Session, *, username: str, password: str) -> UserRecord | None:
    user = get_user_by_username(db, username)
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def get_user_profile(db: Session, user_id: int) -> UserProfileRecord | None:
    return db.get(UserProfileRecord, user_id)


def upsert_user_profile(
    db: Session,
    *,
    user_id: int,
    preferred_pace: str | None = None,
    preferred_start_time: str | None = None,
    preferred_end_time: str | None = None,
    favorite_cities: list[str] | None = None,
    favorite_attraction_types: list[str] | None = None,
    notes: str | None = None,
) -> UserProfileRecord:
    profile = db.get(UserProfileRecord, user_id)
    if not profile:
        profile = UserProfileRecord(user_id=user_id)
        db.add(profile)

    profile.preferred_pace = preferred_pace
    profile.preferred_start_time = preferred_start_time
    profile.preferred_end_time = preferred_end_time
    profile.favorite_cities = _dumps(favorite_cities or [])
    profile.favorite_attraction_types = _dumps(favorite_attraction_types or [])
    profile.notes = notes
    db.commit()
    db.refresh(profile)
    return profile


def profile_to_dict(profile: UserProfileRecord | None, user_id: int) -> dict[str, Any]:
    if not profile:
        return {
            "user_id": user_id,
            "preferred_pace": None,
            "preferred_start_time": None,
            "preferred_end_time": None,
            "favorite_cities": [],
            "favorite_attraction_types": [],
            "notes": None,
            "updated_at": None,
        }
    return {
        "user_id": profile.user_id,
        "preferred_pace": profile.preferred_pace,
        "preferred_start_time": profile.preferred_start_time,
        "preferred_end_time": profile.preferred_end_time,
        "favorite_cities": _loads(profile.favorite_cities),
        "favorite_attraction_types": _loads(profile.favorite_attraction_types),
        "notes": profile.notes,
        "updated_at": profile.updated_at,
    }


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []
