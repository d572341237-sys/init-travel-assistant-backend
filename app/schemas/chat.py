from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


MAX_MESSAGE_LENGTH = 2000
MAX_PROFILE_NOTE_LENGTH = 1000
MAX_PROFILE_LIST_ITEMS = 20
MAX_PROFILE_ITEM_LENGTH = 40
USERNAME_PATTERN = r"^[A-Za-z0-9_\-\u4e00-\u9fff]{3,64}$"
TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
ALLOWED_PACES = {"relaxed", "balanced", "packed"}


def _reject_control_chars(value: str, field_name: str) -> str:
    if any(ord(char) < 32 and char not in {"\n", "\r", "\t"} for char in value):
        raise ValueError(f"{field_name} contains invalid control characters")
    return value


def _clean_text(value: str, field_name: str, max_length: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty")
    if len(cleaned) > max_length:
        raise ValueError(f"{field_name} is too long")
    return _reject_control_chars(cleaned, field_name)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_MESSAGE_LENGTH, description="User natural language input")
    thread_id: str | None = Field(
        default=None,
        description="Compatibility field. Browser session is managed by backend HttpOnly Cookie.",
    )

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        return _clean_text(value, "message", MAX_MESSAGE_LENGTH)


class ChatResponse(BaseModel):
    answer: str
    thread_id: str


class WeatherResponse(BaseModel):
    location: str
    days: int
    result: dict[str, Any]


class RouteContextResponse(BaseModel):
    city: str
    result: dict[str, Any]


class AttractionOptionsResponse(BaseModel):
    city: str
    result: dict[str, Any]


class TravelPlanSummary(BaseModel):
    id: int
    thread_id: str
    user_message: str
    answer: str
    created_at: datetime


class UserRegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, pattern=USERNAME_PATTERN)
    password: str = Field(..., min_length=6, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return _clean_text(str(value), "username", 64)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _reject_control_chars(value, "password")


class UserLoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64, pattern=USERNAME_PATTERN)
    password: str = Field(..., min_length=6, max_length=128)

    @field_validator("username", mode="before")
    @classmethod
    def validate_username(cls, value: str) -> str:
        return _clean_text(str(value), "username", 64)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _reject_control_chars(value, "password")


class UserResponse(BaseModel):
    id: int
    username: str
    created_at: datetime


class UserProfileRequest(BaseModel):
    preferred_pace: str | None = None
    preferred_start_time: str | None = Field(default=None, pattern=TIME_PATTERN)
    preferred_end_time: str | None = Field(default=None, pattern=TIME_PATTERN)
    favorite_cities: list[str] = Field(default_factory=list, max_length=MAX_PROFILE_LIST_ITEMS)
    favorite_attraction_types: list[str] = Field(default_factory=list, max_length=MAX_PROFILE_LIST_ITEMS)
    notes: str | None = Field(default=None, max_length=MAX_PROFILE_NOTE_LENGTH)

    @field_validator("preferred_pace")
    @classmethod
    def validate_preferred_pace(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _clean_text(value, "preferred_pace", 32)
        if cleaned not in ALLOWED_PACES:
            raise ValueError("preferred_pace is invalid")
        return cleaned

    @field_validator("favorite_cities", "favorite_attraction_types")
    @classmethod
    def validate_profile_list(cls, value: list[str]) -> list[str]:
        cleaned_items: list[str] = []
        for item in value:
            cleaned = _clean_text(str(item), "profile item", MAX_PROFILE_ITEM_LENGTH)
            if cleaned not in cleaned_items:
                cleaned_items.append(cleaned)
        return cleaned_items

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _clean_text(value, "notes", MAX_PROFILE_NOTE_LENGTH)


class UserProfileResponse(UserProfileRequest):
    user_id: int
    updated_at: datetime | None = None
