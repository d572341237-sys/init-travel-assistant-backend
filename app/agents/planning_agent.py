from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
from pathlib import Path
from typing import Annotated, Any, Callable, TypedDict

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_mcp_adapters.sessions import StdioConnection
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel, Field

from app.agents.scenic_address_agent import run_scenic_address_agent
from app.agents.weather_agent import build_chat_model, _content_to_text
from app.tools.route import fetch_driving_route_segments, get_tour_route_context
from app.tools.weather import get_weather_forecast


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


PLANNING_SYSTEM_PROMPT = """你是智能旅行助手的总 planning agent。
你的任务是整合天气 agent 和景区地址信息 agent 的结果，生成最终旅行计划。

要求：
1. 严格遵守用户保存的旅行城市、旅行天数、每日开始时间、每日结束时间和行程松紧程度。
2. 只能使用景区地址信息 agent 提供的 POI、地址、区域和交通距离信息，不要编造景点。
3. 结合天气 agent 的结果给出穿衣、带伞和出行安全建议。
4. 如果景点较多，要按天分摊，不要把所有景点塞进同一天。
5. 每个 itinerary_days[].items[].place 必须对应景区地址信息 agent 返回的 route_context.pois 里的具体 POI 名称；不要输出“洛阳市区”“市区名胜”“自由探索”“返程交通枢纽”等泛化地点作为行程项目。
6. 如果用户指定某个景点需要游玩多天，例如“老君山两天”，要优先满足这个要求；其余天数必须用 route_context.pois 中其他 POI 继续安排，不要写空泛推荐。
7. 所有正式行程项目都要尽量包含地址、安排、交通和提醒，格式参考：09:00-10:30 具体景点，地址：具体地址，安排：具体活动，交通：怎么到达，提醒：注意事项。
8. 必须只输出合法 JSON，不要 Markdown，不要代码块，不要 JSON 之外的解释文字。
9. 酒店只推荐入住地段或区域，不推荐具体酒店名称，不编造价格、评分或空房。

JSON 结构必须符合：
{
  "destination": "城市名称",
  "days_count": 3,
  "pace": "relaxed|balanced|compact",
  "daily_time_window": {"start": "09:00", "end": "18:00"},
  "weather_summary": {
    "summary": "天气概述",
    "clothing_advice": "穿衣建议",
    "umbrella_advice": "带伞建议",
    "safety_tips": ["提示1"]
  },
  "itinerary_days": [
    {
      "day": 1,
      "theme": "当天主题",
      "items": [
        {
          "time": "09:00-10:30",
          "place": "景点名称",
          "address": "地址",
          "district": "区域",
          "activity": "游玩安排",
          "transport_tip": "交通提示",
          "notes": "注意事项"
        }
      ]
    }
  ],
  "hotel_area_recommendation": {
    "primary_area": "首选地段",
    "backup_area": "备选地段",
    "reason": "推荐原因"
  },
  "general_tips": ["整体建议"]
}
"""


PLANNING_TOOL_SYSTEM_PROMPT = """你是智能旅行助手的总 planning agent。
在生成最终行程前，你需要像真正的 agent 一样自主判断是否调用工具。

可用工具：
1. get_weather_forecast：当用户要求结合天气、穿衣、带伞、出行安全，或上下文缺少天气数据时调用。
2. get_selected_scenic_address_context：当用户是在上一轮候选景点后选择编号、选择景点名称，或要求“你帮我选/直接规划”时调用。这个工具会读取后端会话中保存的候选景点。
3. get_tour_route_context：当没有上一轮候选景点上下文，但用户直接给出城市和想去的景点/关键词时调用，用于获取高德 POI、地址和景点间驾车信息。

调用原则：
- 不要编造天气、地址、经纬度、驾车距离和耗时。
- 如果用户要正式规划多日行程，通常至少需要天气工具和一个地址/路线类工具。
- 如果已有上下文数据明显足够，可以不重复调用同类工具。
- 工具调用完成后，用一句中文说明已经拿到哪些数据即可，不要在这个阶段输出最终 JSON。
"""


class PlanningToolState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


class SelectedScenicAddressQuery(BaseModel):
    selection_message: str = Field(
        ...,
        description="用户关于景点选择或自动选择的最新自然语言输入，例如：选择1、3、5，或：你帮我选并直接规划。",
    )


async def run_planning_agent(
    message: str,
    weather_result: Any,
    scenic_address_result: dict[str, Any],
    thread_id: str | None = None,
    auto_select_attractions: bool = False,
    auto_fill_remaining_attractions: bool = False,
    additional_attractions: list[str] | None = None,
) -> str:
    return await run_planning_agent_streaming(
        message=message,
        weather_result=weather_result,
        scenic_address_result=scenic_address_result,
        thread_id=thread_id,
        auto_select_attractions=auto_select_attractions,
        auto_fill_remaining_attractions=auto_fill_remaining_attractions,
        additional_attractions=additional_attractions,
    )


async def run_planning_agent_streaming(
    message: str,
    weather_result: Any,
    scenic_address_result: dict[str, Any],
    on_token: Callable[[str], None] | None = None,
    thread_id: str | None = None,
    auto_select_attractions: bool = False,
    auto_fill_remaining_attractions: bool = False,
    additional_attractions: list[str] | None = None,
) -> str:
    if scenic_address_result.get("error"):
        return str(scenic_address_result["error"])

    llm = build_chat_model()
    payload = {
        "user_message": message,
        "weather_result": weather_result,
        "scenic_address_result": scenic_address_result,
    }
    try:
        tool_context = await _run_planning_tool_calling(
            message=message,
            payload=payload,
            thread_id=thread_id,
            auto_select_attractions=auto_select_attractions,
            auto_fill_remaining_attractions=auto_fill_remaining_attractions,
            additional_attractions=additional_attractions or [],
        )
    except Exception:
        logger.exception("planning tool calling failed")
        tool_context = []
    if tool_context:
        payload["planning_tool_results"] = tool_context
    messages = [
        ("system", PLANNING_SYSTEM_PROMPT),
        (
            "human",
            "请根据以下 JSON 生成最终旅行规划：\n"
            f"{json.dumps(payload, ensure_ascii=False)}",
        ),
    ]

    if on_token:
        chunks: list[str] = []
        async for chunk in llm.astream(messages):
            token = _content_to_text(chunk.content) if chunk.content else ""
            if not token:
                continue
            chunks.append(token)
            on_token(token)
        return await _normalize_and_enrich_plan_json("".join(chunks), fallback_payload=payload)

    response = await llm.ainvoke(messages)
    return await _normalize_and_enrich_plan_json(_content_to_text(response.content), fallback_payload=payload)


async def _run_planning_tool_calling(
    message: str,
    payload: dict[str, Any],
    thread_id: str | None,
    auto_select_attractions: bool,
    auto_fill_remaining_attractions: bool,
    additional_attractions: list[str],
) -> list[dict[str, Any]]:
    tools = await _load_planning_tools(
        thread_id=thread_id,
        auto_select_attractions=auto_select_attractions,
        auto_fill_remaining_attractions=auto_fill_remaining_attractions,
        additional_attractions=additional_attractions,
    )
    if not tools:
        return []

    llm_with_tools = build_chat_model().bind_tools(tools)
    tool_node = ToolNode(tools)

    async def agent_node(state: PlanningToolState) -> dict[str, list[BaseMessage]]:
        response = await llm_with_tools.ainvoke(state["messages"])
        return {"messages": [response]}

    def should_continue(state: PlanningToolState) -> str:
        messages = state["messages"]
        last_message = messages[-1] if messages else None
        tool_calls = getattr(last_message, "tool_calls", None) or []
        if tool_calls and _tool_call_rounds(messages) < 3:
            return "tools"
        return END

    graph = StateGraph(PlanningToolState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tool_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    app = graph.compile()

    result = await app.ainvoke(
        {
            "messages": [
                SystemMessage(content=PLANNING_TOOL_SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        "请根据用户最新输入和已有上下文，判断是否需要调用工具补充天气、景点地址或路线数据。\n"
                        f"{json.dumps({'user_message': message, 'existing_context': payload}, ensure_ascii=False)}"
                    )
                ),
            ]
        },
        config={"recursion_limit": 8},
    )
    tool_results = _extract_tool_results(result.get("messages") or [])
    logger.info("planning agent tool calls: %s", [item.get("tool") for item in tool_results])
    return tool_results


def _build_planning_tools(
    thread_id: str | None,
    auto_select_attractions: bool,
    auto_fill_remaining_attractions: bool,
    additional_attractions: list[str],
) -> list[BaseTool]:
    async def get_selected_scenic_address_context(selection_message: str) -> str:
        result = await run_scenic_address_agent(
            message=selection_message,
            thread_id=thread_id,
            auto_select_attractions=auto_select_attractions,
            auto_fill_remaining_attractions=auto_fill_remaining_attractions,
            additional_attractions=additional_attractions,
        )
        return json.dumps(result, ensure_ascii=False)

    selected_context_tool = StructuredTool.from_function(
        coroutine=get_selected_scenic_address_context,
        name="get_selected_scenic_address_context",
        description=(
            "读取当前后端会话中保存的候选景点，根据用户最新选择或自动选择要求，"
            "返回已选景点的高德 POI、地址、经纬度和景点间路线信息。"
        ),
        args_schema=SelectedScenicAddressQuery,
    )
    return [get_weather_forecast, selected_context_tool, get_tour_route_context]


async def _load_planning_tools(
    thread_id: str | None,
    auto_select_attractions: bool,
    auto_fill_remaining_attractions: bool,
    additional_attractions: list[str],
) -> list[BaseTool]:
    connection: StdioConnection = {
        "transport": "stdio",
        "command": sys.executable,
        "args": ["-m", "app.mcp.travel_tools_server"],
        "cwd": str(PROJECT_ROOT),
        "env": _build_mcp_env(
            thread_id=thread_id,
            auto_select_attractions=auto_select_attractions,
            auto_fill_remaining_attractions=auto_fill_remaining_attractions,
            additional_attractions=additional_attractions,
        ),
    }
    try:
        tools = await load_mcp_tools(None, connection=connection, server_name="travel_tools")
        if tools:
            logger.info("planning agent loaded MCP tools: %s", [tool.name for tool in tools])
            return tools
    except Exception:
        logger.exception("failed to load MCP tools, falling back to local tools")

    return _build_planning_tools(
        thread_id=thread_id,
        auto_select_attractions=auto_select_attractions,
        auto_fill_remaining_attractions=auto_fill_remaining_attractions,
        additional_attractions=additional_attractions,
    )


def _build_mcp_env(
    thread_id: str | None,
    auto_select_attractions: bool,
    auto_fill_remaining_attractions: bool,
    additional_attractions: list[str],
) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["TRAVEL_MCP_THREAD_ID"] = thread_id or ""
    env["TRAVEL_MCP_AUTO_SELECT_ATTRACTIONS"] = "true" if auto_select_attractions else "false"
    env["TRAVEL_MCP_AUTO_FILL_REMAINING_ATTRACTIONS"] = "true" if auto_fill_remaining_attractions else "false"
    env["TRAVEL_MCP_ADDITIONAL_ATTRACTIONS"] = json.dumps(additional_attractions, ensure_ascii=False)
    return env


def _tool_call_rounds(messages: list[BaseMessage]) -> int:
    return sum(1 for message in messages if getattr(message, "tool_calls", None))


def _extract_tool_results(messages: list[BaseMessage]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        content = _content_to_text(message.content)
        results.append(
            {
                "tool": getattr(message, "name", "") or "",
                "content": _parse_json_object(content) or content,
            }
        )
    return results


async def _normalize_and_enrich_plan_json(raw_text: str, fallback_payload: dict[str, Any]) -> str:
    plan = json.loads(_normalize_plan_json(raw_text, fallback_payload=fallback_payload))
    live_segments = await _fetch_live_daily_route_segments(plan)
    if live_segments:
        plan["route_segments"] = live_segments
    return json.dumps(plan, ensure_ascii=False)


def _normalize_plan_json(raw_text: str, fallback_payload: dict[str, Any]) -> str:
    parsed = _parse_json_object(raw_text)
    if not parsed:
        parsed = _build_fallback_plan(fallback_payload, raw_text)
    normalized = _normalize_plan_object(parsed, fallback_payload=fallback_payload)
    return json.dumps(normalized, ensure_ascii=False)


def _parse_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        text = match.group(0)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def _fetch_live_daily_route_segments(plan: dict[str, Any]) -> list[dict[str, Any]]:
    requests = _build_live_route_requests(plan)
    if not requests:
        return []

    raw_segments = await fetch_driving_route_segments(requests)
    normalized: list[dict[str, Any]] = []
    for index, segment in enumerate(raw_segments, start=1):
        if not isinstance(segment, dict) or segment.get("error"):
            continue
        distance_m = _safe_optional_int(segment.get("distance_m"))
        duration_s = _safe_optional_int(segment.get("duration_s"))
        polyline = _normalize_polyline(segment.get("polyline"))
        if not polyline:
            origin_location = _normalize_location(segment.get("origin_location"))
            destination_location = _normalize_location(segment.get("destination_location"))
            if origin_location and destination_location:
                polyline = [
                    [origin_location["lng"], origin_location["lat"]],
                    [destination_location["lng"], destination_location["lat"]],
                ]
        normalized.append(
            {
                "day": _safe_int(segment.get("day"), 1),
                "origin": str(segment.get("origin") or ""),
                "destination": str(segment.get("destination") or ""),
                "origin_location": _normalize_location(segment.get("origin_location")),
                "destination_location": _normalize_location(segment.get("destination_location")),
                "distance_m": distance_m,
                "duration_s": duration_s,
                "distance_text": _format_distance(distance_m),
                "duration_text": _format_duration(duration_s),
                "polyline": polyline,
                "order": _safe_int(segment.get("order"), index),
                "provider": "amap_driving",
            }
        )
    return normalized


def _build_live_route_requests(plan: dict[str, Any]) -> list[dict[str, Any]]:
    days = plan.get("itinerary_days") if isinstance(plan.get("itinerary_days"), list) else []
    requests: list[dict[str, Any]] = []
    order = 1
    for day in days:
        if not isinstance(day, dict):
            continue
        day_number = _safe_int(day.get("day"), 1)
        items = day.get("items") if isinstance(day.get("items"), list) else []
        for origin_item, destination_item in zip(items, items[1:]):
            if not isinstance(origin_item, dict) or not isinstance(destination_item, dict):
                continue
            origin_location = _normalize_location(origin_item.get("location"))
            destination_location = _normalize_location(destination_item.get("location"))
            if not origin_location or not destination_location:
                continue
            requests.append(
                {
                    "day": day_number,
                    "order": order,
                    "origin": str(origin_item.get("place") or ""),
                    "destination": str(destination_item.get("place") or ""),
                    "origin_location": origin_location,
                    "destination_location": destination_location,
                }
            )
            order += 1
    return requests


def _normalize_plan_object(plan: dict[str, Any], fallback_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    weather_summary = plan.get("weather_summary") if isinstance(plan.get("weather_summary"), dict) else {}
    hotel = (
        plan.get("hotel_area_recommendation")
        if isinstance(plan.get("hotel_area_recommendation"), dict)
        else {}
    )
    days_count = _safe_int(plan.get("days_count"), 1)
    days = plan.get("itinerary_days") if isinstance(plan.get("itinerary_days"), list) else []
    poi_locations = _build_poi_location_map(fallback_payload or {})
    normalized_days = [_normalize_day(item, poi_locations=poi_locations) for item in days]
    normalized_days = _ensure_itinerary_day_count(normalized_days, days_count)
    route_segments = _build_route_segments(fallback_payload or plan, itinerary_days=normalized_days)
    return {
        "destination": str(plan.get("destination") or ""),
        "days_count": days_count,
        "pace": str(plan.get("pace") or "balanced"),
        "daily_time_window": _normalize_time_window(plan.get("daily_time_window")),
        "weather_summary": {
            "summary": str(weather_summary.get("summary") or ""),
            "clothing_advice": str(weather_summary.get("clothing_advice") or ""),
            "umbrella_advice": str(weather_summary.get("umbrella_advice") or ""),
            "safety_tips": _string_list(weather_summary.get("safety_tips")),
        },
        "itinerary_days": normalized_days,
        "route_segments": route_segments,
        "hotel_area_recommendation": {
            "primary_area": str(hotel.get("primary_area") or ""),
            "backup_area": str(hotel.get("backup_area") or ""),
            "reason": str(hotel.get("reason") or ""),
        },
        "general_tips": _string_list(plan.get("general_tips")),
    }


def _normalize_time_window(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {"start": "09:00", "end": "18:00"}
    return {
        "start": str(value.get("start") or "09:00"),
        "end": str(value.get("end") or "18:00"),
    }


def _normalize_day(value: Any, poi_locations: dict[str, dict[str, float]] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    items = value.get("items") if isinstance(value.get("items"), list) else []
    return {
        "day": _safe_int(value.get("day"), 1),
        "theme": str(value.get("theme") or ""),
        "items": [_normalize_item(item, poi_locations=poi_locations or {}) for item in items],
    }


def _normalize_item(value: Any, poi_locations: dict[str, dict[str, float]] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    place = str(value.get("place") or "")
    return {
        "time": str(value.get("time") or ""),
        "place": place,
        "address": str(value.get("address") or ""),
        "district": str(value.get("district") or ""),
        "location": _normalize_location(value.get("location")) or _find_poi_location(place, poi_locations or {}),
        "activity": str(value.get("activity") or ""),
        "transport_tip": str(value.get("transport_tip") or ""),
        "notes": str(value.get("notes") or ""),
    }


def _ensure_itinerary_day_count(days: list[dict[str, Any]], days_count: int) -> list[dict[str, Any]]:
    days_count = max(1, days_count)
    if not days:
        return [
            {
                "day": day,
                "theme": "行程安排",
                "items": [],
            }
            for day in range(1, days_count + 1)
        ]

    if len(days) == 1 and days_count > 1:
        items = days[0].get("items") if isinstance(days[0].get("items"), list) else []
        if len(items) > 1:
            chunks = _split_evenly(items, days_count)
            return [
                {
                    "day": day,
                    "theme": days[0].get("theme") or "行程安排",
                    "items": chunks[day - 1],
                }
                for day in range(1, days_count + 1)
            ]

    by_day = {_safe_int(day.get("day"), index + 1): day for index, day in enumerate(days)}
    normalized: list[dict[str, Any]] = []
    for day_number in range(1, days_count + 1):
        day = by_day.get(day_number)
        if day:
            day["day"] = day_number
            normalized.append(day)
        else:
            normalized.append(
                {
                    "day": day_number,
                    "theme": "行程安排",
                    "items": [],
                }
            )
    return normalized


def _split_evenly(items: list[Any], bucket_count: int) -> list[list[Any]]:
    buckets: list[list[Any]] = []
    total = len(items)
    start = 0
    for index in range(bucket_count):
        remaining_items = total - start
        remaining_buckets = bucket_count - index
        size = (remaining_items + remaining_buckets - 1) // remaining_buckets
        buckets.append(items[start : start + size])
        start += size
    return buckets


def _find_poi_location(place: str, poi_locations: dict[str, dict[str, float]]) -> dict[str, float] | None:
    if place in poi_locations:
        return poi_locations[place]
    normalized_place = place.strip()
    if not normalized_place:
        return None
    for name, location in poi_locations.items():
        if name and (name in normalized_place or normalized_place in name):
            return location
    return None


def _extract_route_context(source: dict[str, Any]) -> dict[str, Any]:
    scenic = source.get("scenic_address_result")
    if isinstance(scenic, dict):
        route_context = scenic.get("route_context")
        if isinstance(route_context, dict):
            return route_context

    tool_route_context = _extract_route_context_from_tool_results(source.get("planning_tool_results"))
    if tool_route_context:
        return tool_route_context

    route_context = source.get("route_context")
    if isinstance(route_context, dict):
        return route_context

    if isinstance(source.get("pois"), list) or isinstance(source.get("segments"), list):
        return source
    return {}


def _extract_route_context_from_tool_results(tool_results: Any) -> dict[str, Any]:
    if not isinstance(tool_results, list):
        return {}
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, dict):
            continue
        route_context = content.get("route_context")
        if isinstance(route_context, dict):
            return route_context
        if isinstance(content.get("pois"), list) or isinstance(content.get("segments"), list):
            return content
    return {}


def _build_poi_location_map(payload: dict[str, Any]) -> dict[str, dict[str, float]]:
    route_context = _extract_route_context(payload)
    scenic = payload.get("scenic_address_result")
    route_request = scenic.get("route_request") if isinstance(scenic, dict) else {}
    if not isinstance(route_request, dict):
        route_request = {}

    pois = route_context.get("pois") or route_request.get("selected_pois") or []
    locations: dict[str, dict[str, float]] = {}
    if not isinstance(pois, list):
        return locations

    for poi in pois:
        if not isinstance(poi, dict):
            continue
        name = str(poi.get("name") or "").strip()
        location = _normalize_location(poi.get("location"))
        if name and location:
            locations[name] = location
    return locations


def _build_route_segments(
    payload: dict[str, Any],
    itinerary_days: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    route_context = _extract_route_context(payload)
    raw_segments = route_context.get("segments")
    if not isinstance(raw_segments, list):
        raw_segments = payload.get("route_segments") if isinstance(payload.get("route_segments"), list) else []

    if itinerary_days:
        return _build_daily_route_segments(raw_segments, itinerary_days)

    segments: list[dict[str, Any]] = []
    for index, segment in enumerate(raw_segments, start=1):
        if not isinstance(segment, dict):
            continue
        distance_m = _safe_optional_int(segment.get("distance_m"))
        duration_s = _safe_optional_int(segment.get("duration_s"))
        raw_day = _safe_int(segment.get("day"), 0)
        inferred_day = _infer_segment_day(segment, itinerary_days or [])
        if itinerary_days and not raw_day and inferred_day is None:
            continue
        day = raw_day or inferred_day or 1
        segments.append(
            {
                "day": day,
                "origin": str(segment.get("origin") or ""),
                "destination": str(segment.get("destination") or ""),
                "origin_location": _normalize_location(segment.get("origin_location")),
                "destination_location": _normalize_location(segment.get("destination_location")),
                "distance_m": distance_m,
                "duration_s": duration_s,
                "distance_text": _format_distance(distance_m),
                "duration_text": _format_duration(duration_s),
                "polyline": _normalize_polyline(segment.get("polyline")),
                "order": index,
            }
        )
    return segments


def _build_daily_route_segments(
    raw_segments: list[Any],
    itinerary_days: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    raw_segment_index = _build_raw_segment_index(raw_segments)
    segments: list[dict[str, Any]] = []
    order = 1

    for day in itinerary_days:
        day_number = _safe_int(day.get("day"), 1)
        items = day.get("items") if isinstance(day.get("items"), list) else []
        for origin_item, destination_item in zip(items, items[1:]):
            if not isinstance(origin_item, dict) or not isinstance(destination_item, dict):
                continue
            origin = str(origin_item.get("place") or "").strip()
            destination = str(destination_item.get("place") or "").strip()
            if not origin or not destination:
                continue

            raw_segment = _find_matching_raw_segment(origin, destination, raw_segment_index)
            origin_location = _normalize_location(origin_item.get("location"))
            destination_location = _normalize_location(destination_item.get("location"))
            distance_m = _safe_optional_int(raw_segment.get("distance_m")) if raw_segment else None
            duration_s = _safe_optional_int(raw_segment.get("duration_s")) if raw_segment else None
            polyline = _normalize_polyline(raw_segment.get("polyline")) if raw_segment else []
            if not polyline and origin_location and destination_location:
                polyline = [
                    [origin_location["lng"], origin_location["lat"]],
                    [destination_location["lng"], destination_location["lat"]],
                ]
            if distance_m is None and origin_location and destination_location:
                distance_m = _estimate_distance_m(origin_location, destination_location)
            if duration_s is None and distance_m is not None:
                duration_s = _estimate_driving_duration_s(distance_m)

            segments.append(
                {
                    "day": day_number,
                    "origin": origin,
                    "destination": destination,
                    "origin_location": origin_location,
                    "destination_location": destination_location,
                    "distance_m": distance_m,
                    "duration_s": duration_s,
                    "distance_text": _format_distance(distance_m),
                    "duration_text": _format_duration(duration_s),
                    "polyline": polyline,
                    "order": order,
                    "estimated": not bool(raw_segment),
                }
            )
            order += 1

    return segments


def _build_raw_segment_index(raw_segments: list[Any]) -> list[dict[str, Any]]:
    return [segment for segment in raw_segments if isinstance(segment, dict)]


def _find_matching_raw_segment(
    origin: str,
    destination: str,
    raw_segments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for segment in raw_segments:
        raw_origin = str(segment.get("origin") or "").strip()
        raw_destination = str(segment.get("destination") or "").strip()
        if _same_place(origin, raw_origin) and _same_place(destination, raw_destination):
            return segment
    for segment in raw_segments:
        raw_origin = str(segment.get("origin") or "").strip()
        raw_destination = str(segment.get("destination") or "").strip()
        if _same_place(origin, raw_destination) and _same_place(destination, raw_origin):
            return _reverse_segment(segment)
    return None


def _same_place(left: str, right: str) -> bool:
    if not left or not right:
        return False
    return left == right or left in right or right in left


def _reverse_segment(segment: dict[str, Any]) -> dict[str, Any]:
    reversed_segment = dict(segment)
    reversed_segment["origin"] = segment.get("destination")
    reversed_segment["destination"] = segment.get("origin")
    reversed_segment["origin_location"] = segment.get("destination_location")
    reversed_segment["destination_location"] = segment.get("origin_location")
    polyline = _normalize_polyline(segment.get("polyline"))
    if polyline:
        reversed_segment["polyline"] = list(reversed(polyline))
    return reversed_segment


def _infer_segment_day(segment: dict[str, Any], itinerary_days: list[dict[str, Any]]) -> int | None:
    origin = str(segment.get("origin") or "").strip()
    destination = str(segment.get("destination") or "").strip()
    if not origin and not destination:
        return None

    origin_day = _find_place_day(origin, itinerary_days)
    destination_day = _find_place_day(destination, itinerary_days)
    if origin_day and destination_day:
        return origin_day if origin_day == destination_day else None
    if origin_day or destination_day:
        return origin_day or destination_day
    return None


def _find_place_day(target: str, itinerary_days: list[dict[str, Any]]) -> int | None:
    if not target:
        return None
    for day in itinerary_days:
        items = day.get("items") if isinstance(day.get("items"), list) else []
        places = [str(item.get("place") or "").strip() for item in items if isinstance(item, dict)]
        if _contains_place(target, places):
            return _safe_int(day.get("day"), 1)
    return None


def _contains_place(target: str, places: list[str]) -> bool:
    if not target:
        return False
    for place in places:
        if place and (target == place or target in place or place in target):
            return True
    return False


def _format_distance(distance_m: int | None) -> str:
    if distance_m is None:
        return ""
    if distance_m >= 1000:
        return f"{distance_m / 1000:.1f} 公里"
    return f"{distance_m} 米"


def _format_duration(duration_s: int | None) -> str:
    if duration_s is None:
        return ""
    minutes = max(1, round(duration_s / 60))
    if minutes < 60:
        return f"{minutes} 分钟"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    if remaining_minutes:
        return f"{hours} 小时 {remaining_minutes} 分钟"
    return f"{hours} 小时"


def _estimate_distance_m(origin: dict[str, float], destination: dict[str, float]) -> int:
    radius_m = 6371000
    lat1 = math.radians(origin["lat"])
    lat2 = math.radians(destination["lat"])
    delta_lat = math.radians(destination["lat"] - origin["lat"])
    delta_lng = math.radians(destination["lng"] - origin["lng"])
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lng / 2) ** 2
    )
    straight_distance = 2 * radius_m * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return int(straight_distance * 1.35)


def _estimate_driving_duration_s(distance_m: int) -> int:
    average_speed_mps = 25_000 / 3600
    return max(60, int(distance_m / average_speed_mps))


def _normalize_location(value: Any) -> dict[str, float] | None:
    if isinstance(value, str):
        return _parse_lnglat(value)
    if not isinstance(value, dict):
        return None

    lng = value.get("lng", value.get("longitude"))
    lat = value.get("lat", value.get("latitude"))
    try:
        return {"lng": float(lng), "lat": float(lat)}
    except (TypeError, ValueError):
        return None


def _parse_lnglat(value: str) -> dict[str, float] | None:
    if "," not in value:
        return None
    lng_text, lat_text = value.split(",", 1)
    try:
        return {"lng": float(lng_text), "lat": float(lat_text)}
    except ValueError:
        return None


def _normalize_polyline(value: Any) -> list[list[float]]:
    if not isinstance(value, list):
        return []
    points: list[list[float]] = []
    for point in value:
        if isinstance(point, dict):
            location = _normalize_location(point)
            if location:
                points.append([location["lng"], location["lat"]])
            continue
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            try:
                points.append([float(point[0]), float(point[1])])
            except (TypeError, ValueError):
                continue
    return points


def _build_fallback_plan(payload: dict[str, Any], raw_text: str) -> dict[str, Any]:
    scenic = payload.get("scenic_address_result") or {}
    route_request = scenic.get("route_request") or {}
    route_context = scenic.get("route_context") or {}
    pois = route_context.get("pois") or route_request.get("selected_pois") or []
    return {
        "destination": route_request.get("city") or route_context.get("city") or "",
        "days_count": route_request.get("days") or 1,
        "pace": route_request.get("pace") or "balanced",
        "daily_time_window": {
            "start": route_request.get("start_time") or "09:00",
            "end": route_request.get("end_time") or "18:00",
        },
        "weather_summary": {
            "summary": str(payload.get("weather_result") or ""),
            "clothing_advice": "",
            "umbrella_advice": "",
            "safety_tips": [],
        },
        "itinerary_days": [
            {
                "day": 1,
                "theme": "候选景点行程",
                "items": [
                    {
                        "time": "",
                        "place": str(poi.get("name") or ""),
                        "address": str(poi.get("address") or ""),
                        "district": str(poi.get("district") or ""),
                        "location": _normalize_location(poi.get("location")),
                        "activity": "游览",
                        "transport_tip": "",
                        "notes": "",
                    }
                    for poi in pois
                    if isinstance(poi, dict)
                ],
            }
        ],
        "hotel_area_recommendation": {
            "primary_area": "",
            "backup_area": "",
            "reason": "",
        },
        "route_segments": _build_route_segments(payload),
        "general_tips": [raw_text.strip()] if raw_text.strip() else [],
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
