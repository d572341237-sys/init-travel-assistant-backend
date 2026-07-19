import json
import logging
import re
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.agents.route_agent import run_route_agent
from app.agents.travel_agent import run_travel_agent, stream_travel_agent
from app.core.config import get_settings
from app.db.database import SessionLocal, get_db, init_db
from app.repositories.travel_plan_repository import create_travel_plan, list_travel_plans
from app.repositories.user_repository import (
    authenticate_user,
    create_user,
    get_user_by_id,
    get_user_by_username,
    get_user_profile,
    profile_to_dict,
    upsert_user_profile,
)
from app.schemas.chat import (
    AttractionOptionsResponse,
    ChatRequest,
    ChatResponse,
    RouteContextResponse,
    TravelPlanSummary,
    UserLoginRequest,
    UserProfileRequest,
    UserProfileResponse,
    UserRegisterRequest,
    UserResponse,
    WeatherResponse,
)
from app.tools.route import fetch_attraction_options, fetch_route_context
from app.tools.weather import fetch_weather_forecast


logger = logging.getLogger(__name__)
SESSION_COOKIE_NAME = "travel_assistant_thread_id"
USER_COOKIE_NAME = "travel_assistant_user_id"
COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 7
SESSION_COOKIE_MAX_AGE_SECONDS = COOKIE_MAX_AGE_SECONDS
USER_COOKIE_MAX_AGE_SECONDS = COOKIE_MAX_AGE_SECONDS
MAX_LOOKUP_TEXT_LENGTH = 80
SQL_INJECTION_PATTERNS = [
    re.compile(r"\bunion\s+select\b", re.IGNORECASE),
    re.compile(r"\bselect\b.+\bfrom\b", re.IGNORECASE),
    re.compile(r"\b(insert|update|delete|drop|alter|truncate)\b.+\b(table|from|into|set)\b", re.IGNORECASE),
    re.compile(r"\b(or|and)\b\s+['\"]?\w+['\"]?\s*=\s*['\"]?\w+['\"]?", re.IGNORECASE),
]
SQL_META_TOKENS = (";", "--", "/*", "*/", "\x00")
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(self)",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://webapi.amap.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob: https://*.amap.com https://*.autonavi.com; "
        "connect-src 'self' https://webapi.amap.com https://restapi.amap.com https://*.amap.com https://*.autonavi.com"
    ),
}


def _parse_cors_origins(value: str) -> list[str]:
    origins = [item.strip() for item in value.split(",") if item.strip()]
    return origins or ["*"]


app = FastAPI(
    title="智能旅行助手后端 MVP",
    description="LangGraph 多 agent + 高德地图工具调用 MVP",
    version="0.1.0",
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_cors_origins(settings.cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/debug", StaticFiles(directory="static", html=True), name="debug")


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    if request.url.scheme == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=15552000; includeSubDomains")
    return response


@app.on_event("startup")
async def startup() -> None:
    init_db()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


@app.get("/api/config/map")
async def get_map_config() -> dict[str, str | bool]:
    settings = get_settings()
    return {
        "provider": "amap",
        "api_key": settings.amap_js_api_key,
        "security_code": settings.amap_js_security_code,
        "enabled": settings.has_amap_js_credentials,
    }


@app.post("/api/auth/register", response_model=UserResponse)
async def register(
    payload: UserRegisterRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> UserResponse:
    username = payload.username.strip()
    if get_user_by_username(db, username):
        raise HTTPException(status_code=409, detail={"code": "USERNAME_EXISTS", "message": "用户名已存在。"})
    user = create_user(db, username=username, password=payload.password)
    _set_user_cookie(response, user.id)
    return UserResponse(id=user.id, username=user.username, created_at=user.created_at)


@app.post("/api/auth/login", response_model=UserResponse)
async def login(
    payload: UserLoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> UserResponse:
    user = authenticate_user(db, username=payload.username.strip(), password=payload.password)
    if not user:
        raise HTTPException(status_code=401, detail={"code": "INVALID_CREDENTIALS", "message": "用户名或密码错误。"})
    _set_user_cookie(response, user.id)
    return UserResponse(id=user.id, username=user.username, created_at=user.created_at)


@app.post("/api/auth/logout")
async def logout(response: Response) -> dict[str, str]:
    _delete_cookie(response, USER_COOKIE_NAME)
    _delete_cookie(response, SESSION_COOKIE_NAME)
    return {"status": "ok"}


@app.get("/api/auth/me", response_model=UserResponse)
async def me(http_request: Request, db: Session = Depends(get_db)) -> UserResponse:
    user = _get_current_user(http_request, db)
    return UserResponse(id=user.id, username=user.username, created_at=user.created_at)


@app.get("/api/profile", response_model=UserProfileResponse)
async def get_profile(http_request: Request, db: Session = Depends(get_db)) -> UserProfileResponse:
    user = _get_current_user(http_request, db)
    return UserProfileResponse(**profile_to_dict(get_user_profile(db, user.id), user.id))


@app.put("/api/profile", response_model=UserProfileResponse)
async def update_profile(
    payload: UserProfileRequest,
    http_request: Request,
    db: Session = Depends(get_db),
) -> UserProfileResponse:
    user = _get_current_user(http_request, db)
    profile = upsert_user_profile(
        db,
        user_id=user.id,
        preferred_pace=payload.preferred_pace,
        preferred_start_time=payload.preferred_start_time,
        preferred_end_time=payload.preferred_end_time,
        favorite_cities=payload.favorite_cities,
        favorite_attraction_types=payload.favorite_attraction_types,
        notes=payload.notes,
    )
    return UserProfileResponse(**profile_to_dict(profile, user.id))


@app.get("/api/weather", response_model=WeatherResponse)
async def get_weather(
    location: str = Query(..., min_length=1, max_length=MAX_LOOKUP_TEXT_LENGTH),
    days: int = Query(default=3, ge=1, le=7),
) -> WeatherResponse:
    location = _validate_lookup_text(location, "location")
    try:
        result = await fetch_weather_forecast(location=location, days=days)
    except Exception as exc:
        _raise_public_error(
            exc,
            code="WEATHER_QUERY_FAILED",
            message="天气服务暂时不可用，请稍后再试。",
        )
    return WeatherResponse(
        location=location,
        days=days,
        result=result,
    )


@app.get("/api/route/context", response_model=RouteContextResponse)
async def get_route_context(
    city: str = Query(..., min_length=1, max_length=MAX_LOOKUP_TEXT_LENGTH),
    days: int = Query(default=1, ge=1, le=7),
) -> RouteContextResponse:
    city = _validate_lookup_text(city, "city")
    try:
        result = await fetch_route_context(city=city, days=days)
    except Exception as exc:
        _raise_public_error(
            exc,
            code="ROUTE_CONTEXT_QUERY_FAILED",
            message="路线数据服务暂时不可用，请稍后再试。",
        )
    return RouteContextResponse(city=city, result=result)


@app.get("/api/attractions", response_model=AttractionOptionsResponse)
async def get_attractions(
    city: str = Query(..., min_length=1, max_length=MAX_LOOKUP_TEXT_LENGTH),
    max_pois: int = Query(default=8, ge=2, le=12),
) -> AttractionOptionsResponse:
    city = _validate_lookup_text(city, "city")
    try:
        result = await fetch_attraction_options(city=city, max_pois=max_pois)
    except Exception as exc:
        _raise_public_error(
            exc,
            code="ATTRACTION_QUERY_FAILED",
            message="景点数据服务暂时不可用，请稍后再试。",
        )
    return AttractionOptionsResponse(city=city, result=result)


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest, http_request: Request, response: Response) -> ChatResponse:
    thread_id = _resolve_thread_id(http_request.cookies.get(SESSION_COOKIE_NAME))
    user_id = _resolve_user_id(http_request)
    _set_session_cookie(response, thread_id)
    try:
        answer = await run_travel_agent(
            message=request.message,
            thread_id=thread_id,
        )
    except Exception as exc:
        _raise_public_error(
            exc,
            code="AGENT_EXECUTION_FAILED",
            message="旅行助手暂时无法完成请求，请稍后再试。",
        )

    _save_travel_plan(thread_id=thread_id, user_id=user_id, user_message=request.message, answer=answer)
    return ChatResponse(answer=answer, thread_id=thread_id)


@app.post("/api/route", response_model=ChatResponse)
async def route_chat(request: ChatRequest, http_request: Request, response: Response) -> ChatResponse:
    thread_id = _resolve_thread_id(http_request.cookies.get(SESSION_COOKIE_NAME))
    user_id = _resolve_user_id(http_request)
    _set_session_cookie(response, thread_id)
    try:
        answer = await run_route_agent(
            message=request.message,
            thread_id=thread_id,
        )
    except Exception as exc:
        _raise_public_error(
            exc,
            code="ROUTE_AGENT_EXECUTION_FAILED",
            message="路线规划服务暂时无法完成请求，请稍后再试。",
        )

    _save_travel_plan(thread_id=thread_id, user_id=user_id, user_message=request.message, answer=answer)
    return ChatResponse(answer=answer, thread_id=thread_id)


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest, http_request: Request) -> StreamingResponse:
    thread_id = _resolve_thread_id(http_request.cookies.get(SESSION_COOKIE_NAME))
    user_id = _resolve_user_id(http_request)

    async def event_generator() -> AsyncIterator[str]:
        final_answer = ""
        try:
            async for item in stream_travel_agent(
                message=request.message,
                thread_id=thread_id,
            ):
                if item.get("event") == "done":
                    final_answer = (item.get("data") or {}).get("answer") or ""
                yield _to_sse(event=item["event"], data=item["data"])
            if final_answer:
                _save_travel_plan(
                    thread_id=thread_id,
                    user_id=user_id,
                    user_message=request.message,
                    answer=final_answer,
                )
        except Exception as exc:
            logger.exception("stream travel agent failed")
            yield _to_sse(
                event="error",
                data={
                    "code": "AGENT_STREAM_FAILED",
                    "message": "旅行助手暂时无法完成流式请求，请稍后再试。",
                },
            )

    response = StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    _set_session_cookie(response, thread_id)
    return response


@app.get("/api/plans", response_model=list[TravelPlanSummary])
async def get_plans(
    http_request: Request,
    response: Response,
    limit: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[TravelPlanSummary]:
    thread_id = _resolve_thread_id(http_request.cookies.get(SESSION_COOKIE_NAME))
    user_id = _resolve_user_id(http_request)
    _set_session_cookie(response, thread_id)
    return [
        TravelPlanSummary(
            id=item.id,
            thread_id=item.thread_id,
            user_message=item.user_message,
            answer=item.answer,
            created_at=item.created_at,
        )
        for item in list_travel_plans(db, thread_id=thread_id, user_id=user_id, limit=limit)
    ]


def _to_sse(event: str, data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _resolve_thread_id(thread_id: str | None) -> str:
    normalized = (thread_id or "").strip()
    if _is_valid_uuid(normalized):
        return normalized
    return str(uuid4())


def _is_valid_uuid(value: str) -> bool:
    if not value:
        return False
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _validate_lookup_text(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_INPUT", "message": f"{field_name} cannot be empty."},
        )
    if len(cleaned) > MAX_LOOKUP_TEXT_LENGTH:
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_INPUT", "message": f"{field_name} is too long."},
        )
    if any(ord(char) < 32 for char in cleaned):
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_INPUT", "message": f"{field_name} contains invalid characters."},
        )
    if any(token in cleaned for token in SQL_META_TOKENS) or any(
        pattern.search(cleaned) for pattern in SQL_INJECTION_PATTERNS
    ):
        raise HTTPException(
            status_code=422,
            detail={"code": "INVALID_INPUT", "message": f"{field_name} contains unsafe query content."},
        )
    return cleaned


def _set_session_cookie(response: Response, thread_id: str) -> None:
    _set_cookie(response, key=SESSION_COOKIE_NAME, value=thread_id, max_age=SESSION_COOKIE_MAX_AGE_SECONDS)


def _set_user_cookie(response: Response, user_id: int) -> None:
    _set_cookie(response, key=USER_COOKIE_NAME, value=str(user_id), max_age=USER_COOKIE_MAX_AGE_SECONDS)


def _set_cookie(response: Response, key: str, value: str, max_age: int) -> None:
    secure, samesite = _cookie_security_options()
    response.set_cookie(
        key=key,
        value=value,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite=samesite,
        path="/",
    )


def _delete_cookie(response: Response, key: str) -> None:
    secure, samesite = _cookie_security_options()
    response.delete_cookie(key=key, secure=secure, samesite=samesite, path="/")


def _cookie_security_options() -> tuple[bool, str]:
    settings = get_settings()
    secure = settings.cookie_secure
    samesite = settings.cookie_samesite.strip().lower()
    if samesite not in {"lax", "strict", "none"}:
        samesite = "lax"
    if samesite == "none" and not secure:
        samesite = "lax"
    return secure, samesite


def _resolve_user_id(request: Request) -> int | None:
    raw_user_id = request.cookies.get(USER_COOKIE_NAME)
    if not raw_user_id:
        return None
    try:
        return int(raw_user_id)
    except ValueError:
        return None


def _get_current_user(request: Request, db: Session):
    user_id = _resolve_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "请先登录。"})
    user = get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHORIZED", "message": "登录状态已失效，请重新登录。"})
    return user


def _save_travel_plan(thread_id: str, user_id: int | None, user_message: str, answer: str) -> None:
    try:
        with SessionLocal() as db:
            create_travel_plan(
                db,
                thread_id=thread_id,
                user_id=user_id,
                user_message=user_message,
                answer=answer,
            )
    except Exception:
        logger.exception("failed to save travel plan")


def _raise_public_error(exc: Exception, code: str, message: str) -> None:
    logger.exception("request failed: %s", code)
    raise HTTPException(
        status_code=500,
        detail={
            "code": code,
            "message": message,
        },
    ) from exc
