from __future__ import annotations

import logging
import json
import re
from collections.abc import AsyncIterator, Iterator
from functools import lru_cache
from time import monotonic
from typing import Any, Literal, TypedDict
from uuid import UUID, uuid4

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from app.agents.planning_agent import run_planning_agent_streaming
from app.agents.route_agent import (
    _extract_keywords_hint as extract_route_keywords_hint,
    run_route_agent,
)
from app.agents.weather_agent import (
    _content_to_text,
    _extract_days_hint as extract_weather_days_hint,
    _extract_location_hint as extract_weather_location_hint,
    build_chat_model,
    run_weather_agent,
)
from app.core.session_store import get_session_store


logger = logging.getLogger(__name__)

ROUTE_KEYWORDS = (
    "路线",
    "行程",
    "一日游",
    "二日游",
    "三日游",
    "游玩",
    "旅游",
    "旅行规划",
    "怎么安排",
    "景点顺序",
)

HOTEL_KEYWORDS = (
    "酒店",
    "住宿",
    "住哪",
    "住哪里",
    "入住",
    "宾馆",
    "民宿",
)

WEATHER_KEYWORDS = (
    "天气",
    "气温",
    "下雨",
    "降雨",
    "带伞",
    "穿衣",
    "冷不冷",
    "热不热",
)

SUPERVISOR_POLICY_PROMPT = """你是智能旅行助手的总控 agent。
你必须优先判断用户最新输入表达的真实旅游需求，而不是机械沿用上一轮上下文。

判断规则：
1. 如果用户明确提出新的旅游城市、旅行天数、旅行计划或路线需求，应认为这是新的旅行需求，清理上一轮未完成的景点选择上下文，并重新返回新城市的候选景点。
2. 只有当用户明确表达“选择/选/挑/我要/我想去 + 编号或景点名称”，或者输入几乎只包含景点编号时，才可以把它当作上一轮候选景点的选择。
3. 不能因为用户输入中出现数字就判断为景点编号；“2天”“三天”等通常是旅行天数，不是景点选择。
4. 如果用户表达“不知道选哪些、不想选、你帮我决定、按推荐来、直接规划”等含义，且上下文里已经有候选景点、城市和天数，就判断为 auto_selection，让系统从候选景点中自动代选并继续规划。
5. 如果用户第二轮明确说出想去的景点名称，但这些景点不在候选景点列表里，不要忽略；仍可判断为 manual_selection，并把这些列表外景点放入 extra_attractions，后续规划必须一起考虑。
6. 如果用户说“其余几天你帮我安排、剩下的你安排、其他景点按推荐来”等含义，说明用户指定了一部分景点，但剩余行程希望系统从候选景点中自动补足，应设置 auto_fill_remaining_attractions=true。
7. extra_attractions 只放“用户明确想去、但不在候选景点列表中的景点名称”，不要放城市名、天数、节奏、天气、住宿、泛泛兴趣词。
8. 如果结合当前消息和上下文仍无法达到 95% 可信判断，应先追问用户确认真实旅游需求，不要调用下游 agent 继续规划。

你只返回 JSON，不要返回 Markdown。格式：
{
  "action": "manual_selection | auto_selection | new_request | clarify | keep",
  "confidence": 0.0,
  "extra_attractions": ["候选列表外但用户明确想去的景点名称"],
  "auto_fill_remaining_attractions": false,
  "reason": "一句中文理由"
}
"""

AUTO_HOTEL_FOLLOW_UP_QUESTION = "酒店住在哪个地段比较方便？"
WEATHER_AGENT_PUBLIC_ERROR = "天气 agent 暂时无法完成查询，请稍后再试。"
ROUTE_AGENT_PUBLIC_ERROR = "路线规划 agent 暂时无法完成规划，请稍后再试。"
SCENIC_ADDRESS_AGENT_PUBLIC_ERROR = "景区地址信息 agent 暂时无法完成查询，请稍后再试。"
PLANNING_AGENT_PUBLIC_ERROR = "总 planning agent 暂时无法完成规划，请稍后再试。"
AgentMode = Literal[
    "attraction_selection_agent",
    "clarification_agent",
    "planning_agent",
    "route_agent",
    "weather_agent",
]


class TravelState(TypedDict, total=False):
    message: str
    thread_id: str
    selected_agent: AgentMode
    detected_location: str
    weather_message: str
    route_message: str
    weather_result: str
    scenic_address_result: dict[str, Any]
    route_result: str
    planning_result: str
    clarification_result: str
    auto_select_attractions: bool
    auto_fill_remaining_attractions: bool
    additional_attractions: list[str]
    final_answer: str


async def run_travel_graph(message: str, thread_id: str | None = None) -> str:
    state = await build_travel_graph().ainvoke(
        {
            "message": message,
            "thread_id": _resolve_thread_id(thread_id),
        }
    )
    return state.get("final_answer") or "没有生成有效回答。"


async def stream_travel_graph(
    message: str,
    thread_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    initial_state: TravelState = {
        "message": message,
        "thread_id": _resolve_thread_id(thread_id),
    }

    async for stream_mode, chunk in build_travel_graph().astream(
        initial_state,
        stream_mode=["custom", "updates"],
    ):
        if stream_mode == "custom":
            if not isinstance(chunk, dict):
                continue
            yield chunk
            continue

        for node_name, update in chunk.items():
            if not isinstance(update, dict):
                continue

            for item in _format_graph_update(node_name, update):
                yield item


@lru_cache
def build_travel_graph() -> Any:
    graph = StateGraph(TravelState)
    graph.add_node("supervisor", _supervisor_node)
    graph.add_node("weather_agent", _weather_agent_node)
    graph.add_node("route_agent", _route_agent_node)
    graph.add_node("scenic_address_agent", _scenic_address_agent_node)
    graph.add_node("planning_agent", _planning_agent_node)
    graph.add_node("merge", _merge_node)

    graph.add_edge(START, "supervisor")
    graph.add_edge("supervisor", "weather_agent")
    graph.add_edge("supervisor", "route_agent")
    graph.add_edge("supervisor", "scenic_address_agent")
    graph.add_edge(["route_agent", "weather_agent", "scenic_address_agent"], "planning_agent")
    graph.add_edge("planning_agent", "merge")
    graph.add_edge("merge", END)
    return graph.compile()


async def _supervisor_node(state: TravelState) -> dict[str, Any]:
    message = state["message"]
    thread_id = state.get("thread_id")
    pending_attractions = (
        get_session_store().get_pending_attraction_choices(thread_id)
        if thread_id
        else None
    )

    location = extract_weather_location_hint(message)
    days = extract_weather_days_hint(message)
    selected_agent = select_agent(message)
    clarification_result = ""
    auto_select_attractions = False
    auto_fill_remaining_attractions = False
    additional_attractions: list[str] = []

    if pending_attractions:
        supervisor_decision = await _classify_pending_attraction_intent(
            message=message,
            location=location,
            pending_attractions=pending_attractions,
        )
        action = supervisor_decision.get("action")
        confidence = _safe_confidence(supervisor_decision.get("confidence"))
        additional_attractions = list(supervisor_decision.get("extra_attractions") or [])
        auto_fill_remaining_attractions = bool(supervisor_decision.get("auto_fill_remaining_attractions"))
        if action == "new_request" and confidence >= 0.95:
            if thread_id:
                get_session_store().clear_pending_attraction_choices(thread_id)
            selected_agent = "attraction_selection_agent"
        elif action == "manual_selection" and confidence >= 0.95:
            selected_agent = "planning_agent"
            location = pending_attractions.get("city") or location
            days = pending_attractions.get("days") or days
        elif action == "auto_selection" and confidence >= 0.95:
            selected_agent = "planning_agent"
            auto_select_attractions = True
            location = pending_attractions.get("city") or location
            days = pending_attractions.get("days") or days
        elif action == "keep" and confidence >= 0.95:
            selected_agent = selected_agent
        else:
            selected_agent = "clarification_agent"
            pending_city = pending_attractions.get("city") or "上一轮城市"
            clarification_result = (
                f"我还保留着{pending_city}的候选景点列表，但你刚才的输入没有让我足够确定"
                "是要继续选择上一轮景点、让我自动代选景点，还是要开始新的旅行计划。\n\n"
                "请你明确回复一种需求：\n"
                "1. 如果继续上一轮，请回复：选择 1、3、5\n"
                "2. 如果不想自己选，请回复：你帮我选并直接规划\n"
                "3. 如果有候选外景点，请回复：选择 1、3，另外我还想去南澳岛\n"
                "4. 如果开始新计划，请回复：我要去泉州旅游2天"
            )
    elif _should_ask_for_city(message, selected_agent, location):
        selected_agent = "clarification_agent"
        clarification_result = (
            "我还没有识别出你想去的城市。请告诉我具体目的地，例如：\n"
            "帮我规划泉州2天旅行，轻松一点，每天9点开始18点结束。"
        )
    elif _should_collect_attractions_first(message, selected_agent, location):
        selected_agent = "attraction_selection_agent"

    weather_message = message
    if location:
        if pending_attractions and selected_agent == "planning_agent":
            weather_message = (
                f"查询{location}未来{days}天天气，并给出带伞、穿衣和旅行安全建议。"
                f"注意：天气查询地点只能使用旅行城市{location}，不要改用用户第二轮补充的景点名称。"
            )
        else:
            weather_message = (
                f"查询{location}未来{days}天天气，并给出带伞、穿衣和旅行安全建议。"
                f"原始用户需求：{message}"
            )

    route_message = message
    if location and location not in message:
        route_message = f"{message}\n\n总控 agent 识别到的旅行城市：{location}"

    return {
        "selected_agent": selected_agent,
        "detected_location": location or "",
        "weather_message": weather_message,
        "route_message": route_message,
        "clarification_result": clarification_result,
        "auto_select_attractions": auto_select_attractions,
        "auto_fill_remaining_attractions": auto_fill_remaining_attractions,
        "additional_attractions": additional_attractions,
    }


async def _weather_agent_node(state: TravelState) -> dict[str, Any]:
    if state.get("selected_agent") != "weather_agent":
        return {}

    writer = get_stream_writer()
    writer({"event": "node_start", "data": {"node": "weather_agent"}})

    try:
        result = await run_weather_agent(
            message=state.get("weather_message") or state["message"],
            thread_id=_agent_thread_id(state.get("thread_id"), "weather"),
        )
    except Exception:
        logger.exception("weather agent node failed")
        result = WEATHER_AGENT_PUBLIC_ERROR
    finally:
        writer({"event": "node_end", "data": {"node": "weather_agent"}})
    return {"weather_result": result}


async def _route_agent_node(state: TravelState) -> dict[str, Any]:
    if state.get("selected_agent") not in {
        "attraction_selection_agent",
        "route_agent",
    }:
        return {}

    try:
        result = await run_route_agent(
            message=state.get("route_message") or state["message"],
            thread_id=state.get("thread_id"),
        )
    except Exception:
        logger.exception("route agent node failed")
        result = ROUTE_AGENT_PUBLIC_ERROR
    return {"route_result": result}


async def _scenic_address_agent_node(state: TravelState) -> dict[str, Any]:
    # Planning agent now resolves scenic address context through its own LangGraph ToolNode loop.
    return {}


async def _planning_agent_node(state: TravelState) -> dict[str, Any]:
    selected_agent = state.get("selected_agent")
    if selected_agent == "clarification_agent":
        return {
            "planning_result": _stringify_agent_result(
                state.get("clarification_result"),
                "总控 agent 需要确认需求",
            )
        }
    if selected_agent == "weather_agent":
        return {
            "planning_result": _stringify_agent_result(
                state.get("weather_result"),
                "天气 agent 执行失败",
            )
        }
    elif selected_agent in {"attraction_selection_agent", "route_agent"}:
        return {
            "planning_result": _stringify_agent_result(
                state.get("route_result"),
                "路线规划 agent 执行失败",
            )
        }

    writer = get_stream_writer()
    writer({"event": "node_start", "data": {"node": "planning_agent"}})
    try:
        token_buffer: list[str] = []
        last_flush = monotonic()

        def flush_tokens(force: bool = False) -> None:
            nonlocal last_flush
            if not token_buffer:
                return
            content = "".join(token_buffer)
            if not force and len(content) < 240 and monotonic() - last_flush < 0.6:
                return
            token_buffer.clear()
            last_flush = monotonic()
            writer(
                {
                    "event": "token",
                    "data": {"node": "planning_agent", "content": content},
                }
            )

        def on_token(token: str) -> None:
            token_buffer.append(token)
            flush_tokens()

        result = await run_planning_agent_streaming(
            message=state["message"],
            weather_result=state.get("weather_result"),
            scenic_address_result=state.get("scenic_address_result") or {},
            on_token=on_token,
            thread_id=state.get("thread_id"),
            auto_select_attractions=bool(state.get("auto_select_attractions")),
            auto_fill_remaining_attractions=bool(state.get("auto_fill_remaining_attractions")),
            additional_attractions=state.get("additional_attractions") or [],
        )
        flush_tokens(force=True)
    except Exception:
        logger.exception("planning agent node failed")
        result = PLANNING_AGENT_PUBLIC_ERROR
    finally:
        writer({"event": "node_end", "data": {"node": "planning_agent"}})
    return {"planning_result": result}


async def _merge_node(state: TravelState) -> dict[str, Any]:
    return {
        "final_answer": _stringify_agent_result(
            state.get("planning_result"),
            "总 planning agent 执行失败",
        )
    }


def select_agent(message: str) -> AgentMode:
    if _is_route_follow_up(message):
        return "route_agent"
    if any(keyword in message for keyword in ROUTE_KEYWORDS):
        return "planning_agent"
    if any(keyword in message for keyword in WEATHER_KEYWORDS):
        return "weather_agent"
    return "planning_agent"


def _is_route_follow_up(message: str) -> bool:
    stripped = message.strip()
    if re.search(r"(?:^|我|就)?(?:选择|选|挑|我要|我想去)\s*[\d一二三四五六七八九十、,，和与 ]+", stripped):
        return True
    if any(keyword in message for keyword in HOTEL_KEYWORDS):
        return True
    return False


def _should_collect_attractions_first(
    message: str,
    selected_agent: AgentMode,
    location: str | None,
) -> bool:
    if selected_agent != "planning_agent":
        return False
    if not location:
        return False
    if extract_route_keywords_hint(message):
        return False
    return any(keyword in message for keyword in ROUTE_KEYWORDS)


def _should_ask_for_city(
    message: str,
    selected_agent: AgentMode,
    location: str | None,
) -> bool:
    if location:
        return False
    if selected_agent not in {"planning_agent", "route_agent"}:
        return False
    return _has_travel_request_intent(message)


async def _classify_pending_attraction_intent(
    message: str,
    location: str | None,
    pending_attractions: dict[str, Any],
) -> dict[str, Any]:
    pois = pending_attractions.get("pois") or []
    context = {
        "current_user_message": message,
        "detected_location_from_current_message": location,
        "pending_city": pending_attractions.get("city"),
        "pending_days": pending_attractions.get("days"),
        "pending_pace": pending_attractions.get("pace"),
        "pending_time_window": {
            "start": pending_attractions.get("start_time"),
            "end": pending_attractions.get("end_time"),
        },
        "candidate_attractions": [
            {
                "index": index,
                "name": poi.get("name"),
                "type": poi.get("type"),
                "address": poi.get("address"),
            }
            for index, poi in enumerate(pois, start=1)
        ],
    }

    try:
        llm = build_chat_model()
        response = await llm.ainvoke(
            [
                ("system", SUPERVISOR_POLICY_PROMPT),
                (
                    "human",
                    "请判断用户最新输入相对于上一轮候选景点上下文的真实意图。\n"
                    f"{json.dumps(context, ensure_ascii=False)}",
                ),
            ]
        )
    except Exception:
        logger.exception("supervisor intent classification failed")
        return {
            "action": "clarify",
            "confidence": 0,
            "reason": "总控 agent 判断失败，需要追问用户确认。",
        }

    parsed = _parse_json_object(_content_to_text(response.content))
    if not parsed:
        return {
            "action": "clarify",
            "confidence": 0,
            "reason": "总控 agent 未返回有效 JSON，需要追问用户确认。",
        }
    action = str(parsed.get("action") or "").strip()
    if action not in {"manual_selection", "auto_selection", "new_request", "clarify", "keep"}:
        action = "clarify"
    return {
        "action": action,
        "confidence": _safe_confidence(parsed.get("confidence")),
        "extra_attractions": _normalize_extra_attractions(parsed.get("extra_attractions")),
        "auto_fill_remaining_attractions": bool(parsed.get("auto_fill_remaining_attractions")),
        "reason": str(parsed.get("reason") or ""),
    }


def _parse_json_object(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.S)
    if match:
        text = match.group(0)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _safe_confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(1, parsed))


def _normalize_extra_attractions(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    normalized: list[str] = []
    for value in values:
        text = str(value or "").strip(" ，,。！？?、")
        if not text:
            continue
        if any(token in text for token in ("天", "点", "天气", "酒店", "住宿", "轻松", "紧凑", "行程")):
            continue
        if text not in normalized:
            normalized.append(text)
    return normalized[:8]


def _has_travel_request_intent(message: str) -> bool:
    travel_keywords = ROUTE_KEYWORDS + (
        "去",
        "到",
        "想去",
        "我要去",
        "计划",
        "规划",
        "安排",
        "旅行",
        "旅游",
        "两天",
        "三天",
    )
    return any(keyword in message for keyword in travel_keywords)


def _is_hotel_area_question(message: str) -> bool:
    hotel_keywords = ("酒店", "住宿", "住哪", "住哪里", "入住", "住在", "宾馆", "民宿")
    area_keywords = ("地段", "区域", "片区", "哪里", "哪儿", "附近", "方便")
    return any(keyword in message for keyword in hotel_keywords) and any(
        keyword in message for keyword in area_keywords
    )


def _should_auto_follow_up_hotel(route_result: str) -> bool:
    text = str(route_result or "").strip()
    if not text:
        return False

    incomplete_route_markers = (
        "请告诉我要规划哪个城市",
        "请选择想去的景点后我再规划路线",
        "你可以回复编号",
        "获取景点候选失败",
        "执行失败",
        "暂时无法",
        "我还没有可参考的路线规划",
    )
    return not any(marker in text for marker in incomplete_route_markers)


def _stringify_agent_result(result: Any, fallback_prefix: str) -> str:
    text = str(result or "").strip()
    if not text:
        return f"{fallback_prefix}：未生成有效结果。"
    return text


def _format_graph_update(node_name: str, update: dict[str, Any]) -> Iterator[dict[str, Any]]:
    if node_name == "supervisor" and update.get("selected_agent"):
        yield {
            "event": "agent_selected",
            "data": {
                "agent": update["selected_agent"],
                "detected_location": update.get("detected_location") or None,
            },
        }
        if update["selected_agent"] == "planning_agent":
            yield {
                "event": "agents_started",
                "data": {
                    "agents": ["weather_agent", "scenic_address_agent", "planning_agent"],
                    "mode": "langgraph_parallel",
                },
            }
        return

    if node_name == "weather_agent" and update.get("weather_result"):
        yield {
            "event": "message",
            "data": {"node": node_name, "content": update["weather_result"]},
        }
        return

    if node_name == "route_agent" and update.get("route_result"):
        yield {
            "event": "message",
            "data": {"node": node_name, "content": update["route_result"]},
        }
        return

    if node_name == "planning_agent" and update.get("planning_result"):
        yield {
            "event": "message",
            "data": {"node": node_name, "content": update["planning_result"]},
        }
        return

    if node_name == "merge" and update.get("final_answer"):
        yield {"event": "done", "data": {"answer": update["final_answer"]}}


def _agent_thread_id(thread_id: str | None, agent_name: str) -> str | None:
    if not thread_id:
        return None
    return f"{thread_id}:{agent_name}"


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
