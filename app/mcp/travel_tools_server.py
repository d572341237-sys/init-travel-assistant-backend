from __future__ import annotations

import json
import logging
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.agents.scenic_address_agent import run_scenic_address_agent
from app.tools.route import fetch_route_context
from app.tools.weather import fetch_weather_forecast


logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

mcp = FastMCP("travel-assistant-tools")


@mcp.tool(
    name="get_weather_forecast",
    description="查询指定城市或地区未来 1 到 7 天高德天气，并返回穿衣、带伞和行程天气判断所需原始数据。",
)
async def get_weather_forecast(location: str, days: int = 3) -> dict[str, Any]:
    return await fetch_weather_forecast(location=location, days=days)


@mcp.tool(
    name="get_tour_route_context",
    description="根据城市、景点关键词和旅行天数查询高德 POI、地址、经纬度与景点间驾车路线信息。",
)
async def get_tour_route_context(
    city: str,
    keywords: list[str] | None = None,
    days: int = 1,
    max_pois: int = 6,
) -> dict[str, Any]:
    return await fetch_route_context(
        city=city,
        keywords=keywords or [],
        days=days,
        max_pois=max_pois,
    )


@mcp.tool(
    name="get_selected_scenic_address_context",
    description=(
        "读取当前会话中上一轮保存的候选景点，按用户最新选择或自动选择要求返回已选景点地址、经纬度和路线信息。"
    ),
)
async def get_selected_scenic_address_context(selection_message: str) -> dict[str, Any]:
    thread_id = os.getenv("TRAVEL_MCP_THREAD_ID") or None
    additional_attractions = _json_list(os.getenv("TRAVEL_MCP_ADDITIONAL_ATTRACTIONS"))
    return await run_scenic_address_agent(
        message=selection_message,
        thread_id=thread_id,
        auto_select_attractions=_env_bool("TRAVEL_MCP_AUTO_SELECT_ATTRACTIONS"),
        auto_fill_remaining_attractions=_env_bool("TRAVEL_MCP_AUTO_FILL_REMAINING_ATTRACTIONS"),
        additional_attractions=additional_attractions,
    )


def _env_bool(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


if __name__ == "__main__":
    mcp.run(transport="stdio")
