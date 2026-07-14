import hashlib
import json
import logging
from typing import Any

import httpx
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.core.config import get_settings


logger = logging.getLogger(__name__)

AMAP_DISTRICT_URL = "https://restapi.amap.com/v3/config/district"
AMAP_PLACE_TEXT_URL = "https://restapi.amap.com/v3/place/text"
AMAP_DRIVING_URL = "https://restapi.amap.com/v3/direction/driving"


class RouteContextQuery(BaseModel):
    city: str = Field(..., description="旅游城市或行政区，例如：泉州、北京、厦门")
    keywords: list[str] = Field(default_factory=list, description="用户明确想去的景点或兴趣关键词")
    days: int = Field(default=1, ge=1, le=7, description="行程天数，1 到 7 天")
    max_pois: int = Field(default=6, ge=2, le=12, description="最多返回的候选地点数量")


async def fetch_route_context(
    city: str,
    keywords: list[str] | None = None,
    days: int = 1,
    max_pois: int = 6,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.has_amap_credentials:
        return {
            "city": city,
            "error": "缺少 AMAP_API_KEY，无法调用高德地图 API。",
        }

    keywords = [item.strip() for item in (keywords or []) if item.strip()]
    days = max(1, min(days, 7))
    max_pois = max(2, min(max_pois, 12))

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            district = await _resolve_district(client, city)
            if district.get("error"):
                return district

            pois = await _collect_pois(client, district=district, keywords=keywords, max_pois=max_pois)
            if not pois:
                return {
                    "city": city,
                    "matched_city": district,
                    "error": "高德 POI 搜索没有找到可用于规划路线的地点。",
                }

            segments = await _build_driving_segments(client, pois[:max_pois])
    except httpx.HTTPError:
        logger.exception("amap route context request failed")
        return {
            "city": city,
            "error": "高德路线服务暂时不可用，请稍后再试。",
        }

    return {
        "provider": "amap",
        "task": "tour_route_context",
        "city": city,
        "days": days,
        "matched_city": district,
        "pois": pois[:max_pois],
        "segments": segments,
        "summary": {
            "poi_count": len(pois[:max_pois]),
            "total_driving_distance_m": sum(item.get("distance_m") or 0 for item in segments),
            "total_driving_duration_s": sum(item.get("duration_s") or 0 for item in segments),
        },
    }


async def fetch_attraction_options(
    city: str,
    max_pois: int = 8,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.has_amap_credentials:
        return {
            "city": city,
            "error": "缺少 AMAP_API_KEY，无法调用高德地图 API。",
        }

    max_pois = max(2, min(max_pois, 12))
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            district = await _resolve_district(client, city)
            if district.get("error"):
                return district

            pois = await _collect_pois(
                client,
                district=district,
                keywords=[],
                max_pois=max_pois,
            )
    except httpx.HTTPError:
        logger.exception("amap attraction options request failed")
        return {
            "city": city,
            "error": "高德景点服务暂时不可用，请稍后再试。",
        }

    if not pois:
        return {
            "city": city,
            "matched_city": district,
            "error": "高德 POI 搜索没有找到可推荐的景点。",
        }

    return {
        "provider": "amap",
        "task": "attraction_options",
        "city": city,
        "matched_city": district,
        "pois": pois[:max_pois],
    }


async def _collect_pois(
    client: httpx.AsyncClient,
    district: dict[str, Any],
    keywords: list[str],
    max_pois: int,
) -> list[dict[str, Any]]:
    pois: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    if keywords:
        for keyword in keywords:
            payload = await _search_pois(client, district=district, keyword=keyword, offset=3)
            for poi in payload.get("pois", []):
                normalized = _normalize_poi(poi)
                if not normalized or normalized["id"] in seen_ids:
                    continue
                seen_ids.add(normalized["id"])
                pois.append(normalized)
                break

        if len(pois) >= max_pois:
            return pois[:max_pois]
        return pois

    search_keywords = ["景点"] if keywords else ["景点"]
    for keyword in search_keywords:
        payload = await _search_pois(client, district=district, keyword=keyword, offset=max_pois)
        for poi in payload.get("pois", []):
            if not _is_attraction_poi(poi):
                continue
            normalized = _normalize_poi(poi)
            if not normalized or normalized["id"] in seen_ids:
                continue
            seen_ids.add(normalized["id"])
            pois.append(normalized)
            if len(pois) >= max_pois:
                return pois

    return pois


async def _resolve_district(client: httpx.AsyncClient, city: str) -> dict[str, Any]:
    payload = await _amap_get(
        client,
        AMAP_DISTRICT_URL,
        {
            "keywords": city,
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
            "city": city,
            "error": f"高德行政区查询没有找到城市：{city}",
        }

    district = districts[0]
    return {
        "name": district.get("name"),
        "adcode": district.get("adcode"),
        "citycode": district.get("citycode"),
        "level": district.get("level"),
    }


async def _search_pois(
    client: httpx.AsyncClient,
    district: dict[str, Any],
    keyword: str,
    offset: int,
) -> dict[str, Any]:
    params = {
        "keywords": keyword,
        "city": district.get("adcode") or district.get("name") or "",
        "citylimit": "true",
        "offset": str(offset),
        "page": "1",
        "extensions": "base",
        "output": "JSON",
    }
    if keyword == "景点":
        params["types"] = "110000|110100|110200"

    return await _amap_get(
        client,
        AMAP_PLACE_TEXT_URL,
        params,
    )


async def _build_driving_segments(
    client: httpx.AsyncClient,
    pois: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for origin, destination in zip(pois, pois[1:]):
        payload = await _amap_get(
            client,
            AMAP_DRIVING_URL,
            {
                "origin": origin["location"],
                "destination": destination["location"],
                "strategy": "0",
                "extensions": "base",
                "output": "JSON",
            },
        )
        route = payload.get("route") or {}
        paths = route.get("paths") or []
        first_path = paths[0] if paths else {}
        segments.append(
            {
                "origin": origin["name"],
                "destination": destination["name"],
                "origin_location": origin["location"],
                "destination_location": destination["location"],
                "distance_m": _to_int(first_path.get("distance")),
                "duration_s": _to_int(first_path.get("duration")),
                "polyline": _collect_path_polyline(first_path),
                "error": payload.get("error"),
            }
        )
    return segments


async def fetch_driving_route_segments(
    requests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.has_amap_credentials or not requests:
        return []

    segments: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for request in requests:
                origin_location = _format_location(request.get("origin_location"))
                destination_location = _format_location(request.get("destination_location"))
                if not origin_location or not destination_location:
                    continue

                payload = await _amap_get(
                    client,
                    AMAP_DRIVING_URL,
                    {
                        "origin": origin_location,
                        "destination": destination_location,
                        "strategy": "0",
                        "extensions": "base",
                        "output": "JSON",
                    },
                )
                route = payload.get("route") or {}
                paths = route.get("paths") or []
                first_path = paths[0] if paths else {}
                segments.append(
                    {
                        "day": request.get("day"),
                        "order": request.get("order"),
                        "origin": request.get("origin"),
                        "destination": request.get("destination"),
                        "origin_location": origin_location,
                        "destination_location": destination_location,
                        "distance_m": _to_int(first_path.get("distance")),
                        "duration_s": _to_int(first_path.get("duration")),
                        "polyline": _collect_path_polyline(first_path),
                        "provider": "amap_driving",
                        "error": payload.get("error"),
                    }
                )
    except httpx.HTTPError:
        logger.exception("amap daily driving route request failed")
        return []

    return segments


def _format_location(value: Any) -> str:
    if isinstance(value, str) and "," in value:
        return value
    if isinstance(value, dict):
        lng = value.get("lng", value.get("longitude"))
        lat = value.get("lat", value.get("latitude"))
        if lng is not None and lat is not None:
            return f"{lng},{lat}"
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return f"{value[0]},{value[1]}"
    return ""


def _collect_path_polyline(path: dict[str, Any]) -> list[list[float]]:
    points: list[list[float]] = []
    steps = path.get("steps") if isinstance(path, dict) else []
    if not isinstance(steps, list):
        return points

    for step in steps:
        if not isinstance(step, dict):
            continue
        for point in _parse_polyline(str(step.get("polyline") or "")):
            if not points or points[-1] != point:
                points.append(point)
    return points


def _parse_polyline(polyline: str) -> list[list[float]]:
    points: list[list[float]] = []
    for item in polyline.split(";"):
        item = item.strip()
        if not item or "," not in item:
            continue
        lng_text, lat_text = item.split(",", 1)
        try:
            points.append([float(lng_text), float(lat_text)])
        except ValueError:
            continue
    return points


async def _amap_get(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, str],
) -> dict[str, Any]:
    response = await client.get(url, params=_with_amap_auth(params))
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
        signed_params["sig"] = _build_amap_signature(signed_params, settings.amap_private_key)
    return signed_params


def _build_amap_signature(params: dict[str, str], private_key: str) -> str:
    sorted_query = "&".join(
        f"{key}={value}"
        for key, value in sorted(params.items())
        if key != "sig"
    )
    return hashlib.md5(f"{sorted_query}{private_key}".encode("utf-8")).hexdigest()


def _normalize_poi(poi: dict[str, Any]) -> dict[str, Any] | None:
    location = poi.get("location")
    if not location or "," not in location:
        return None

    return {
        "id": poi.get("id") or f"{poi.get('name')}:{location}",
        "name": poi.get("name"),
        "type": poi.get("type"),
        "typecode": poi.get("typecode"),
        "address": poi.get("address"),
        "district": poi.get("adname"),
        "location": location,
    }


def _is_attraction_poi(poi: dict[str, Any]) -> bool:
    typecode = str(poi.get("typecode") or "")
    return typecode.startswith("11")


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


@tool(args_schema=RouteContextQuery)
async def get_tour_route_context(
    city: str,
    keywords: list[str] | None = None,
    days: int = 1,
    max_pois: int = 6,
) -> str:
    """查询旅游路线规划需要的高德 POI 和景点间驾车距离/耗时。"""

    try:
        result = await fetch_route_context(
            city=city,
            keywords=keywords,
            days=days,
            max_pois=max_pois,
        )
    except httpx.HTTPError:
        logger.exception("route tool request failed")
        result = {
            "city": city,
            "error": "高德路线服务暂时不可用，请稍后再试。",
        }

    return json.dumps(result, ensure_ascii=False)
