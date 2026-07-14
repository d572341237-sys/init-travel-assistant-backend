from collections.abc import AsyncIterator
from functools import lru_cache
from inspect import signature
import json
import re
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent

from app.core.config import get_settings
from app.tools.weather import (
    fetch_weather_forecast,
    get_weather_forecast,
    reset_weather_location_guard,
    set_weather_location_guard,
)


SYSTEM_PROMPT = """你是一个智能旅行助手后端 MVP 中的天气规划 agent。

目标：
1. 当用户询问城市、地区、景点的天气、是否适合出行、是否需要带伞或穿衣建议时，优先调用 get_weather_forecast 工具。
2. 工具 location 参数必须来自用户当前消息中明确出现的地点，不要使用示例地点，不要自行替换成其他城市。
3. 如果用户没有给出地点，要先追问地点，不要编造天气。
4. 回答要简洁、中文优先，结合温度、降水概率、风速给出旅行建议。
5. 不要规划景点路线、不要安排游玩顺序；酒店、景点、交通规划由其他 agent 负责。
"""

LOCATION_ALIASES: dict[str, tuple[str, ...]] = {
    "北京": ("北京", "北京市", "beijing"),
    "上海": ("上海", "上海市", "shanghai"),
    "广州": ("广州", "广州市", "guangzhou"),
    "深圳": ("深圳", "深圳市", "shenzhen"),
    "杭州": ("杭州", "杭州市", "西湖", "杭州西湖", "hangzhou"),
    "成都": ("成都", "成都市", "chengdu"),
    "重庆": ("重庆", "重庆市", "chongqing"),
    "南京": ("南京", "南京市", "nanjing"),
    "苏州": ("苏州", "苏州市", "suzhou"),
    "西安": ("西安", "西安市", "xian", "xi'an"),
    "武汉": ("武汉", "武汉市", "wuhan"),
    "长沙": ("长沙", "长沙市", "changsha"),
    "泉州": ("泉州", "泉州市", "quanzhou"),
    "汕头": ("汕头", "汕头市", "shantou"),
    "厦门": ("厦门", "厦门市", "xiamen"),
    "青岛": ("青岛", "青岛市", "qingdao"),
    "三亚": ("三亚", "三亚市", "sanya"),
    "东京": ("东京", "tokyo"),
    "大阪": ("大阪", "osaka"),
    "首尔": ("首尔", "seoul"),
    "新加坡": ("新加坡", "singapore"),
    "曼谷": ("曼谷", "bangkok"),
    "巴黎": ("巴黎", "paris"),
    "伦敦": ("伦敦", "london"),
    "纽约": ("纽约", "new york"),
}

INVALID_LOCATION_HINTS = {
    "并结合",
    "结合",
    "根据",
    "参考",
    "顺便",
    "同时",
    "以及",
    "还有",
    "天气",
    "行程",
    "路线",
    "旅行",
    "旅游",
    "帮我规划",
    "帮我安排",
    "我要去",
    "我想去",
    "计划去",
    "规划",
    "安排",
}


@lru_cache
def build_chat_model() -> ChatOpenAI:
    settings = get_settings()
    if not settings.has_llm_credentials:
        raise RuntimeError("DEEPSEEK_API_KEY is required to run the LangGraph agent.")

    model_kwargs: dict[str, Any] = {
        "model": settings.deepseek_model,
        "api_key": settings.deepseek_api_key,
        "temperature": 0.0,
    }
    if settings.deepseek_base_url:
        model_kwargs["base_url"] = settings.deepseek_base_url

    return ChatOpenAI(**model_kwargs)


@lru_cache
def build_weather_agent() -> Any:
    llm = build_chat_model()
    tools = [get_weather_forecast]

    create_agent_signature = signature(create_react_agent)
    if "prompt" in create_agent_signature.parameters:
        return create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)
    if "state_modifier" in create_agent_signature.parameters:
        return create_react_agent(llm, tools, state_modifier=SYSTEM_PROMPT)
    if "messages_modifier" in create_agent_signature.parameters:
        return create_react_agent(llm, tools, messages_modifier=SYSTEM_PROMPT)
    return create_react_agent(llm, tools)


async def run_weather_agent(message: str, thread_id: str | None = None) -> str:
    location_hint = _extract_location_hint(message)
    if location_hint:
        days = _extract_days_hint(message)
        weather_data = await fetch_weather_forecast(location=location_hint, days=days)
        return await _generate_weather_answer(message, weather_data)

    agent = build_weather_agent()
    config = _build_config(thread_id)
    guard_token = set_weather_location_guard(location_hint)
    try:
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=message)]},
            config=config,
        )
    finally:
        reset_weather_location_guard(guard_token)
    messages = result.get("messages", [])

    for item in reversed(messages):
        if isinstance(item, AIMessage) and item.content:
            return _content_to_text(item.content)

    return "没有生成有效回答。"


async def run_weather_agent_streaming(
    message: str,
    thread_id: str | None = None,
    on_token: Callable[[str], None] | None = None,
) -> str:
    location_hint = _extract_location_hint(message)
    if location_hint:
        days = _extract_days_hint(message)
        weather_data = await fetch_weather_forecast(location=location_hint, days=days)
        return await _generate_weather_answer(
            message,
            weather_data,
            on_token=on_token,
        )

    final_answer = await run_weather_agent(message=message, thread_id=thread_id)
    if on_token and final_answer:
        on_token(final_answer)
    return final_answer


async def stream_weather_agent(
    message: str,
    thread_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    agent = build_weather_agent()
    config = _build_config(thread_id)

    location_hint = _extract_location_hint(message)
    if location_hint:
        days = _extract_days_hint(message)
        yield {
            "event": "tool_call",
            "data": {
                "node": "controlled_weather",
                "name": "get_weather_forecast",
                "args": {"location": location_hint, "days": days},
            },
        }
        weather_data = await fetch_weather_forecast(location=location_hint, days=days)
        yield {
            "event": "tool_result",
            "data": {
                "node": "controlled_weather",
                "name": "get_weather_forecast",
                "content": json.dumps(weather_data, ensure_ascii=False),
            },
        }
        final_answer = await _generate_weather_answer(message, weather_data)
        yield {
            "event": "message",
            "data": {
                "node": "controlled_weather",
                "content": final_answer,
            },
        }
        yield {
            "event": "done",
            "data": {
                "answer": final_answer,
            },
        }
        return

    final_answer = ""
    guard_token = set_weather_location_guard(location_hint)
    try:
        async for chunk in agent.astream(
            {"messages": [HumanMessage(content=message)]},
            config=config,
            stream_mode="updates",
        ):
            for node_name, node_payload in chunk.items():
                messages = node_payload.get("messages", [])
                if not messages:
                    continue

                latest_message = messages[-1]
                if isinstance(latest_message, AIMessage):
                    tool_calls = getattr(latest_message, "tool_calls", None) or []
                    for tool_call in tool_calls:
                        yield {
                            "event": "tool_call",
                            "data": {
                                "node": node_name,
                                "name": tool_call.get("name"),
                                "args": tool_call.get("args"),
                            },
                        }

                    if latest_message.content:
                        final_answer = _content_to_text(latest_message.content)
                        yield {
                            "event": "message",
                            "data": {
                                "node": node_name,
                                "content": final_answer,
                            },
                        }

                elif isinstance(latest_message, ToolMessage):
                    yield {
                        "event": "tool_result",
                        "data": {
                            "node": node_name,
                            "name": latest_message.name,
                            "content": latest_message.content,
                        },
                    }
    finally:
        reset_weather_location_guard(guard_token)

    yield {
        "event": "done",
        "data": {
            "answer": final_answer,
        },
    }


def _build_config(thread_id: str | None) -> dict[str, Any]:
    if not thread_id:
        return {}
    return {"configurable": {"thread_id": thread_id}}


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "".join(parts).strip()
    return str(content)


def _extract_location_hint(message: str) -> str | None:
    lowered_message = message.lower()
    matches: list[tuple[int, str]] = []

    for canonical_location, aliases in LOCATION_ALIASES.items():
        for alias in aliases:
            position = lowered_message.find(alias.lower())
            if position >= 0:
                matches.append((position, canonical_location))
                break

    if not matches:
        return _extract_generic_chinese_location(message)

    matches.sort(key=lambda item: item[0])
    return matches[0][1]


def _extract_generic_chinese_location(message: str) -> str | None:
    patterns = [
        r"(?:帮我查一下|帮我查|查一下|查询|查|看看)(?P<location>[\u4e00-\u9fff]{2,8}?)(?:市)?(?:未来\s*)?[1-7一二三四五六七]\s*天(?:天气|旅行|旅游|出行|适合)?",
        r"(?:规划|安排|设计|做)?(?:未来)?\s*[1-7一二两三四五六七]\s*天(?:的)?(?P<location>[\u4e00-\u9fff]{2,8}?)(?:市)?(?:旅游|旅行|路线|行程|游玩)",
        r"(?:规划|安排|设计|做)?(?:未来)?\s*[1-7一二两三四五六七]\s*天(?P<location>[\u4e00-\u9fff]{2,8}?)(?:市)?(?:的)?(?:旅游|旅行|路线|行程|游玩)",
        r"(?:规划|安排|设计|做)(?P<location>[\u4e00-\u9fff]{2,8}?)(?:市)?(?:一日游|二日游|三日游|[1-7一二两三四五六七]\s*天|旅游|旅行|路线|行程)",
        r"(?P<location>[\u4e00-\u9fff]{2,8}?)(?:市)?(?:一日游|二日游|三日游|[1-7一二两三四五六七]\s*天|旅游路线|旅行路线|游玩路线|行程)",
        r"(?:帮我查一下|帮我查|查一下|查询|查|看看)(?P<location>[\u4e00-\u9fff]{2,8}?)(?:市)?(?:未来|明天|后天|天气|旅行|旅游|出行|适合)",
        r"(?:去|到)(?P<location>[\u4e00-\u9fff]{2,8}?)(?:市)?(?:旅行|旅游|出行|玩|天气)",
        r"(?P<location>[\u4e00-\u9fff]{2,8}?)(?:市)?(?:未来\s*[1-7一二三四五六七]\s*天|明天|后天|天气)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            location = match.group("location").strip()
            cleaned = _clean_location_hint(location)
            if cleaned:
                return cleaned
    return None


def _clean_location_hint(location: str) -> str | None:
    location = location.strip(" ，,。！？?的")
    location = re.sub(r"^(帮我查一下|帮我查|查一下|查询|查|看看|我想|我要|我明天|我后天|请问|一下)", "", location)
    location = re.sub(r"(?:未来.*)$", "", location)
    location = location.strip(" ，,。！？?的")
    if location in INVALID_LOCATION_HINTS:
        return None
    if any(keyword in location for keyword in ("规划", "安排", "帮我", "我要", "我想", "计划")):
        return None
    if len(location) < 2:
        return None
    return location


def _extract_days_hint(message: str) -> int:
    match = re.search(r"(?:未来\s*)?([1-7一二两三四五六七])\s*天", message)
    if not match:
        if "明天" in message:
            return 2
        return 3

    value = match.group(1)
    chinese_digits = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
    }
    if value in chinese_digits:
        return chinese_digits[value]
    return int(value)


async def _generate_weather_answer(
    message: str,
    weather_data: dict[str, Any],
    on_token: Callable[[str], None] | None = None,
) -> str:
    answer = _format_weather_answer(weather_data)
    if on_token:
        on_token(answer)
    return answer


def _format_weather_answer(weather_data: dict[str, Any]) -> str:
    if weather_data.get("error"):
        location = weather_data.get("requested_location") or weather_data.get("location") or "该地点"
        return f"天气服务暂时无法查询到{location}的可用数据：{weather_data['error']}"

    matched = weather_data.get("matched_location") or {}
    location = matched.get("name") or weather_data.get("requested_location") or "目的地"
    current = weather_data.get("current") or {}
    daily = weather_data.get("daily") if isinstance(weather_data.get("daily"), list) else []

    lines = ["天气与出行建议"]
    current_parts = []
    if current.get("weather"):
        current_parts.append(str(current["weather"]))
    if current.get("temperature_c") is not None:
        current_parts.append(f"{current['temperature_c']}℃")
    if current.get("humidity_percent") is not None:
        current_parts.append(f"湿度{current['humidity_percent']}%")
    if current.get("wind_direction") or current.get("wind_power"):
        current_parts.append(f"{current.get('wind_direction') or ''}风{current.get('wind_power') or ''}级".strip())
    if current_parts:
        lines.append(f"- {location}当前天气：{'，'.join(current_parts)}。")

    if daily:
        forecast_text = "；".join(_format_daily_weather(day) for day in daily if isinstance(day, dict))
        if forecast_text:
            lines.append(f"- 未来{len(daily)}天预报：{forecast_text}。")
    else:
        lines.append(f"- 暂未获取到{location}未来几天的逐日预报。")

    lines.append(f"- 穿衣：{_build_clothing_advice(current, daily)}")
    lines.append(f"- 雨具：{_build_umbrella_advice(daily)}")
    for tip in _build_weather_safety_tips(daily):
        lines.append(f"- {tip}")
    return "\n".join(lines)


def _format_daily_weather(day: dict[str, Any]) -> str:
    date = day.get("date") or "未知日期"
    weather = day.get("weather") or "天气未知"
    min_temp = day.get("temperature_min_c")
    max_temp = day.get("temperature_max_c")
    if min_temp is not None and max_temp is not None:
        return f"{date}{weather}，{min_temp}~{max_temp}℃"
    return f"{date}{weather}"


def _build_clothing_advice(current: dict[str, Any], daily: list[Any]) -> str:
    temperatures = []
    if current.get("temperature_c") is not None:
        temperatures.append(current["temperature_c"])
    for day in daily:
        if not isinstance(day, dict):
            continue
        for key in ("temperature_min_c", "temperature_max_c"):
            if day.get(key) is not None:
                temperatures.append(day[key])
    if not temperatures:
        return "建议穿舒适便装和适合步行的鞋子，并根据体感增减衣物。"

    min_temp = min(temperatures)
    max_temp = max(temperatures)
    if max_temp >= 32:
        return "白天气温较高，建议穿短袖、轻薄透气衣物和舒适运动鞋，随身准备防晒用品；早晚或室内空调环境可备薄外套。"
    if min_temp <= 15:
        return "早晚偏凉，建议穿长袖、外套和舒适运动鞋，山区或夜间活动可再加一层保暖衣物。"
    return "建议穿轻便长短袖搭配舒适运动鞋，早晚可备薄外套，长时间步行注意透气和防晒。"


def _build_umbrella_advice(daily: list[Any]) -> str:
    if _has_rain_weather(daily):
        return "预报包含降雨，建议随身携带折叠伞或轻便雨衣，景区步道注意防滑。"
    return "暂未看到明显降雨信号，可备一把轻便折叠伞，同时注意防晒。"


def _has_rain_weather(daily: list[Any]) -> bool:
    rain_keywords = ("雨", "雷", "阵雨", "暴雨", "雪")
    for day in daily:
        if isinstance(day, dict) and any(keyword in str(day.get("weather") or "") for keyword in rain_keywords):
            return True
    return False


def _build_weather_safety_tips(daily: list[Any]) -> list[str]:
    tips = ["长时间户外游览注意补水、防晒，并根据天气变化及时调整行程。"]
    if _has_rain_weather(daily):
        tips.append("雨天道路湿滑，参观石窟、山地或台阶较多的景区时放慢速度。")
        tips.append("如遇雷雨天气，避免在山顶、开阔广场、水边和金属设施附近停留。")
    return tips
