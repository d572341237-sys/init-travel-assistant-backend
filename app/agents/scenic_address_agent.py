from __future__ import annotations

from typing import Any

from app.agents.route_agent import (
    _merge_route_preferences,
    _resolve_pending_selection,
    _store_last_route_context,
)
from app.tools.route import fetch_route_context


async def run_scenic_address_agent(
    message: str,
    thread_id: str | None = None,
    auto_select_attractions: bool = False,
    auto_fill_remaining_attractions: bool = False,
    additional_attractions: list[str] | None = None,
) -> dict[str, Any]:
    selected_request = _resolve_pending_selection(
        message,
        thread_id,
        auto_select_attractions=auto_select_attractions,
        auto_fill_remaining_attractions=auto_fill_remaining_attractions,
        additional_attractions=additional_attractions or [],
    )
    if not selected_request:
        return {
            "error": "没有找到上一轮候选景点选择。请先输入旅行城市，并选择景点或说明让系统自动代选。",
        }

    selected_request = _merge_route_preferences(selected_request, message)
    route_context = await fetch_route_context(
        city=selected_request["city"],
        keywords=selected_request["keywords"],
        days=selected_request["days"],
        max_pois=max(2, len(selected_request["keywords"])),
    )
    if route_context.get("error") and selected_request.get("selected_pois"):
        route_context = {
            "provider": "session_cache",
            "task": "selected_scenic_address_context",
            "city": selected_request["city"],
            "days": selected_request["days"],
            "pois": selected_request["selected_pois"],
            "segments": [],
            "summary": {
                "poi_count": len(selected_request["selected_pois"]),
                "total_driving_distance_m": None,
                "total_driving_duration_s": None,
            },
            "warning": route_context["error"],
        }
    _store_last_route_context(thread_id, selected_request, route_context)

    return {
        "route_request": selected_request,
        "route_context": route_context,
    }
