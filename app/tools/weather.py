import hashlib
import json
import logging
import re
from typing import Any

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.core.config import get_settings


logger = logging.getLogger(__name__)

AMAP_DISTRICT_URL = "https://restapi.amap.com/v3/config/district"
AMAP_WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"
_REQUESTED_LOCATION_GUARD: str | None = None


class WeatherQuery(BaseModel):
    location: str = Field(..., description="用户当前消息中明确出现的城市、地区或景点名称，不得使用示例地点或自行替换地点")
    days: int = Field(default=3, ge=1, le=7, description="查询未来天数，1 到 7 天")


def set_weather_location_guard(location: str | None) -> str | None:
    global _REQUESTED_LOCATION_GUARD
    previous_location = _REQUESTED_LOCATION_GUARD
    _REQUESTED_LOCATION_GUARD = location
    return previous_location


def reset_weather_location_guard(previous_location: str | None) -> None:
    global _REQUESTED_LOCATION_GUARD
    _REQUESTED_LOCATION_GUARD = previous_location


async def fetch_weather_forecast(location: str, days: int = 3) -> dict[str, Any]:
    settings = get_settings()
    if not settings.has_amap_credentials:
        return {
            "location": location,
            "error": "缺少 AMAP_API_KEY，无法调用高德天气 API。",
        }

    days = max(1, min(days, 7))

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            district = await _resolve_district(client, location)
            if district.get("error"):
                return district

            adcode = district["adcode"]
            live_weather = await _fetch_amap_weather(client, adcode=adcode, extensions="base")
            forecast_weather = await _fetch_amap_weather(client, adcode=adcode, extensions="all")
    except httpx.HTTPError:
        logger.exception("amap weather request failed")
        return {
            "location": location,
            "error": "高德天气服务暂时不可用，请稍后再试。",
        }

    if live_weather.get("error"):
        return live_weather
    if forecast_weather.get("error"):
        return forecast_weather

    return _normalize_amap_weather(
        requested_location=location,
        district=district,
        live_weather=live_weather,
        forecast_weather=forecast_weather,
        days=days,
    )


async def _resolve_district(client: httpx.AsyncClient, location: str) -> dict[str, Any]:
    if re.fullmatch(r"\d{6}", location):
        return {
            "requested_location": location,
            "name": location,
            "adcode": location,
            "citycode": None,
            "level": "adcode",
        }

    payload = await _amap_get(
        client,
        AMAP_DISTRICT_URL,
        {
            "keywords": location,
            "subdistrict": "0",
            "extensions": "base",
            "output": "JSON",
        },
    )
    if payload.get("error"):
        return payload

    districts = payload.get("districts") or []
    if not districts:
        return {
            "location": location,
            "error": f"高德行政区查询没有找到地点：{location}",
        }

    district = districts[0]
    return {
        "requested_location": location,
        "name": district.get("name"),
        "adcode": district.get("adcode"),
        "citycode": district.get("citycode"),
        "level": district.get("level"),
    }


async def _fetch_amap_weather(
    client: httpx.AsyncClient,
    adcode: str,
    extensions: str,
) -> dict[str, Any]:
    return await _amap_get(
        client,
        AMAP_WEATHER_URL,
        {
            "city": adcode,
            "extensions": extensions,
            "output": "JSON",
        },
    )


async def _amap_get(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, str],
) -> dict[str, Any]:
    signed_params = _with_amap_auth(params)
    response = await client.get(url, params=signed_params)
    response.raise_for_status()
    payload = response.json()

    if payload.get("status") != "1":
        return {
            "error": payload.get("info") or "高德 API 请求失败",
            "infocode": payload.get("infocode"),
        }

    return payload


def _with_amap_auth(params: dict[str, str]) -> dict[str, str]:
    settings = get_settings()
    signed_params = {**params, "key": settings.amap_api_key}
    if settings.amap_private_key:
        signed_params["sig"] = _build_amap_signature(
            signed_params,
            settings.amap_private_key,
        )
    return signed_params


def _build_amap_signature(params: dict[str, str], private_key: str) -> str:
    sorted_query = "&".join(
        f"{key}={value}"
        for key, value in sorted(params.items())
        if key != "sig"
    )
    raw = f"{sorted_query}{private_key}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _normalize_amap_weather(
    requested_location: str,
    district: dict[str, Any],
    live_weather: dict[str, Any],
    forecast_weather: dict[str, Any],
    days: int,
) -> dict[str, Any]:
    lives = live_weather.get("lives") or []
    forecasts = forecast_weather.get("forecasts") or []
    live = lives[0] if lives else {}
    forecast = forecasts[0] if forecasts else {}

    casts = forecast.get("casts") or []
    daily = [
        {
            "date": item.get("date"),
            "week": item.get("week"),
            "weather": _join_day_night(item.get("dayweather"), item.get("nightweather")),
            "temperature_min_c": _to_number(item.get("nighttemp")),
            "temperature_max_c": _to_number(item.get("daytemp")),
            "day_wind_direction": item.get("daywind"),
            "night_wind_direction": item.get("nightwind"),
            "day_wind_power": item.get("daypower"),
            "night_wind_power": item.get("nightpower"),
            "precipitation_probability_max_percent": None,
        }
        for item in casts[:days]
    ]

    return {
        "provider": "amap",
        "requested_location": requested_location,
        "matched_location": {
            "name": district.get("name") or forecast.get("city") or live.get("city"),
            "adcode": district.get("adcode") or forecast.get("adcode") or live.get("adcode"),
            "citycode": district.get("citycode"),
            "province": forecast.get("province") or live.get("province"),
            "level": district.get("level"),
        },
        "current": {
            "report_time": live.get("reporttime"),
            "weather": live.get("weather"),
            "temperature_c": _to_number(live.get("temperature")),
            "humidity_percent": _to_number(live.get("humidity")),
            "wind_direction": live.get("winddirection"),
            "wind_power": live.get("windpower"),
        },
        "daily": daily,
        "raw": {
            "live_report_time": live.get("reporttime"),
            "forecast_report_time": forecast.get("reporttime"),
        },
    }


def _join_day_night(day_weather: str | None, night_weather: str | None) -> str | None:
    if day_weather and night_weather and day_weather != night_weather:
        return f"{day_weather}转{night_weather}"
    return day_weather or night_weather


def _to_number(value: Any) -> int | float | None:
    if value in (None, "", "暂无"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number.is_integer():
        return int(number)
    return number


@tool(args_schema=WeatherQuery)
async def get_weather_forecast(location: str, days: int = 3) -> str:
    """查询用户当前消息中明确地点的未来 1 到 7 天天气预报。"""

    original_location = location
    guarded_location = _REQUESTED_LOCATION_GUARD
    if guarded_location and not _is_same_location(original_location, guarded_location):
        location = guarded_location

    try:
        result = await fetch_weather_forecast(location=location, days=days)
    except httpx.HTTPError:
        logger.exception("weather tool request failed")
        result = {
            "location": location,
            "error": "高德天气服务暂时不可用，请稍后再试。",
        }

    if guarded_location and original_location != location:
        result["tool_location_overridden"] = {
            "model_requested_location": original_location,
            "user_requested_location": location,
            "reason": "用户输入中已经明确给出地点，工具层按用户地点查询，避免模型误选其他城市。",
        }

    return json.dumps(result, ensure_ascii=False)


def _is_same_location(left: str, right: str) -> bool:
    normalized_left = left.strip().lower()
    normalized_right = right.strip().lower()
    return (
        normalized_left == normalized_right
        or normalized_left in normalized_right
        or normalized_right in normalized_left
    )
