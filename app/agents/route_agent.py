import json
import re
from collections.abc import AsyncIterator
from typing import Any

from app.agents.weather_agent import build_chat_model, _content_to_text
from app.core.session_store import get_session_store
from app.tools.route import fetch_attraction_options, fetch_route_context


ROUTE_SYSTEM_PROMPT = """你是智能旅行助手的路线 agent。
你的任务是基于高德地图返回的 POI 和路段距离信息，规划可执行的旅游路线。
要求：
1. 不要编造未出现在地图数据里的景点名称。
2. 如果地图数据不足，要明确说明缺口，并给出保守建议。
3. 严格遵守用户给出的旅行天数、每日开始时间、每日结束时间和行程节奏。
4. 如果景点较多，要分摊到多天，不要把所有景点塞进同一天。
5. 输出中文，结构清晰，包含每日安排、游玩顺序、交通/距离提示、时间安排和注意事项。
6. 路线规划正文不要输出住宿地段建议；住宿地段由 hotel_area_agent 自动追问并单独输出。
"""

async def run_route_agent(message: str, thread_id: str | None = None) -> str:
    selected_request = _resolve_pending_selection(message, thread_id)
    if selected_request:
        selected_request = _merge_route_preferences(selected_request, message)
        route_context = await fetch_route_context(
            city=selected_request["city"],
            keywords=selected_request["keywords"],
            days=selected_request["days"],
            max_pois=max(2, len(selected_request["keywords"])),
        )
        _store_last_route_context(thread_id, selected_request, route_context)
        return await _generate_route_answer(message, selected_request, route_context)

    hotel_answer = await _maybe_answer_hotel_area(message, thread_id)
    if hotel_answer:
        return hotel_answer

    route_request = await _extract_route_request(message)
    city = route_request.get("city")
    if not city:
        return "请告诉我要规划哪个城市的旅游路线，例如：帮我规划泉州一日游路线。"

    if not route_request.get("keywords"):
        options = await fetch_attraction_options(city=city)
        _attach_planning_preferences(options, route_request)
        _store_pending_options(thread_id, route_request, options)
        return _format_attraction_options(options)

    route_context = await fetch_route_context(
        city=city,
        keywords=route_request.get("keywords") or [],
        days=route_request.get("days") or 1,
        max_pois=route_request.get("max_pois") or 6,
    )
    _store_last_route_context(thread_id, route_request, route_context)
    return await _generate_route_answer(message, route_request, route_context)


async def stream_route_agent(
    message: str,
    thread_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    selected_request = _resolve_pending_selection(message, thread_id)
    if selected_request:
        selected_request = _merge_route_preferences(selected_request, message)
        tool_args = {
            "city": selected_request["city"],
            "keywords": selected_request["keywords"],
            "days": selected_request["days"],
            "max_pois": max(2, len(selected_request["keywords"])),
        }
        yield {
            "event": "tool_call",
            "data": {
                "node": "route_agent",
                "name": "get_tour_route_context",
                "args": tool_args,
            },
        }
        route_context = await fetch_route_context(**tool_args)
        _store_last_route_context(thread_id, selected_request, route_context)
        yield {
            "event": "tool_result",
            "data": {
                "node": "route_agent",
                "name": "get_tour_route_context",
                "content": json.dumps(route_context, ensure_ascii=False),
            },
        }
        answer = await _generate_route_answer(message, selected_request, route_context)
        yield {"event": "message", "data": {"node": "route_agent", "content": answer}}
        yield {"event": "done", "data": {"answer": answer}}
        return

    hotel_answer = await _maybe_answer_hotel_area(message, thread_id)
    if hotel_answer:
        yield {"event": "message", "data": {"node": "route_agent", "content": hotel_answer}}
        yield {"event": "done", "data": {"answer": hotel_answer}}
        return

    route_request = await _extract_route_request(message)
    city = route_request.get("city")
    if not city:
        answer = "请告诉我要规划哪个城市的旅游路线，例如：帮我规划泉州一日游路线。"
        yield {"event": "message", "data": {"node": "route_agent", "content": answer}}
        yield {"event": "done", "data": {"answer": answer}}
        return

    if not route_request.get("keywords"):
        yield {
            "event": "tool_call",
            "data": {
                "node": "route_agent",
                "name": "get_attraction_options",
                "args": {"city": city, "max_pois": 8},
            },
        }
        options = await fetch_attraction_options(city=city)
        _attach_planning_preferences(options, route_request)
        _store_pending_options(thread_id, route_request, options)
        yield {
            "event": "tool_result",
            "data": {
                "node": "route_agent",
                "name": "get_attraction_options",
                "content": json.dumps(options, ensure_ascii=False),
            },
        }
        answer = _format_attraction_options(options)
        yield {"event": "message", "data": {"node": "route_agent", "content": answer}}
        yield {"event": "done", "data": {"answer": answer}}
        return

    tool_args = {
        "city": city,
        "keywords": route_request.get("keywords") or [],
        "days": route_request.get("days") or 1,
        "max_pois": route_request.get("max_pois") or 6,
    }
    yield {
        "event": "tool_call",
        "data": {
            "node": "route_agent",
            "name": "get_tour_route_context",
            "args": tool_args,
        },
    }

    route_context = await fetch_route_context(**tool_args)
    _store_last_route_context(thread_id, route_request, route_context)
    yield {
        "event": "tool_result",
        "data": {
            "node": "route_agent",
            "name": "get_tour_route_context",
            "content": json.dumps(route_context, ensure_ascii=False),
        },
    }

    answer = await _generate_route_answer(message, route_request, route_context)
    yield {"event": "message", "data": {"node": "route_agent", "content": answer}}
    yield {"event": "done", "data": {"answer": answer}}


async def _extract_route_request(message: str) -> dict[str, Any]:
    fallback = _fallback_route_request(message)
    llm = build_chat_model()
    response = await llm.ainvoke(
        [
            (
                "system",
                "从用户旅游路线需求中抽取 JSON。只返回 JSON，不要 Markdown。"
                "字段：city(string|null), days(integer), keywords(array[string]), max_pois(integer), "
                "pace(string|null), start_time(string|null), end_time(string|null)。"
                "keywords 只放用户明确提到的景点/兴趣关键词；没有则为空数组。"
                "pace 只能是 compact、relaxed、balanced；start_time/end_time 用 HH:MM。",
            ),
            ("human", message),
        ]
    )
    text = _content_to_text(response.content)
    parsed = _parse_json_object(text)
    if not parsed:
        return fallback

    return {
        "city": parsed.get("city") or fallback.get("city"),
        "days": _clamp_int(parsed.get("days") or fallback.get("days") or 1, 1, 7),
        "keywords": _normalize_keywords(parsed.get("keywords") or fallback.get("keywords") or []),
        "max_pois": _clamp_int(parsed.get("max_pois") or 6, 2, 12),
        "pace": _normalize_pace(parsed.get("pace") or fallback.get("pace")),
        "start_time": parsed.get("start_time") or fallback.get("start_time"),
        "end_time": parsed.get("end_time") or fallback.get("end_time"),
    }


async def _generate_route_answer(
    message: str,
    route_request: dict[str, Any],
    route_context: dict[str, Any],
) -> str:
    llm = build_chat_model()
    response = await llm.ainvoke(
        [
            ("system", ROUTE_SYSTEM_PROMPT),
            (
                "human",
                "用户需求："
                f"{message}\n\n"
                "结构化需求："
                f"{json.dumps(route_request, ensure_ascii=False)}\n\n"
                "高德地图数据："
                f"{json.dumps(route_context, ensure_ascii=False)}\n\n"
                "请输出路线规划。重要约束："
                "1. 只以结构化需求 keywords 和高德地图数据 pois 作为已选择景点；"
                "2. 不要重新解释用户回复中的编号；"
                "3. 不要声称缺失未出现在 keywords 中的景点；"
                "4. 不要输出住宿地段建议、酒店推荐或住宿区域分析；"
                "5. 若包含 error，请说明当前地图数据获取失败的原因。",
            ),
        ]
    )
    return _content_to_text(response.content)


async def _maybe_answer_hotel_area(message: str, thread_id: str | None) -> str | None:
    if not _is_hotel_area_question(message):
        return None

    cached = get_session_store().get_last_route_context(thread_id) if thread_id else None
    if not cached:
        return "我还没有可参考的路线规划。请先完成路线规划后，我再根据景点分布推荐适合入住的地区。"

    llm = build_chat_model()
    hotel_area_context = _build_hotel_area_context(cached["route_context"])
    response = await llm.ainvoke(
        [
            (
                "system",
                "你是旅行规划助手，任务是根据已规划路线推荐酒店入住地段。"
                "只能推荐地区/片区，不推荐具体酒店名称，不编造酒店价格、评分或空房。",
            ),
            (
                "human",
                "用户问题："
                f"{message}\n\n"
                "路线需求："
                f"{json.dumps(cached['route_request'], ensure_ascii=False)}\n\n"
                "高德路线数据："
                f"{json.dumps(cached['route_context'], ensure_ascii=False)}\n\n"
                "住宿地段分析："
                f"{json.dumps(hotel_area_context, ensure_ascii=False)}\n\n"
                "请给出首选地段、备选地段、适合人群、理由和不推荐区域。",
            ),
        ]
    )
    return _content_to_text(response.content)


def _fallback_route_request(message: str) -> dict[str, Any]:
    return {
        "city": _extract_city_hint(message),
        "days": _extract_days_hint(message),
        "keywords": _extract_keywords_hint(message),
        "max_pois": 6,
        "pace": _extract_pace_hint(message),
        "start_time": _extract_start_time_hint(message),
        "end_time": _extract_end_time_hint(message),
    }


def _store_last_route_context(
    thread_id: str | None,
    route_request: dict[str, Any],
    route_context: dict[str, Any],
) -> None:
    if not thread_id:
        return
    get_session_store().set_last_route_context(
        thread_id,
        {
            "route_request": route_request,
            "route_context": route_context,
        },
    )


def _is_hotel_area_question(message: str) -> bool:
    hotel_keywords = ("酒店", "住宿", "住哪", "住哪里", "入住", "住在", "宾馆", "民宿")
    area_keywords = ("地段", "区域", "片区", "哪里", "哪儿", "附近", "方便")
    return any(keyword in message for keyword in hotel_keywords) and any(
        keyword in message for keyword in area_keywords
    )


def _build_hotel_area_context(route_context: dict[str, Any]) -> dict[str, Any]:
    pois = route_context.get("pois") or []
    districts: dict[str, int] = {}
    for poi in pois:
        district = poi.get("district")
        if district:
            districts[district] = districts.get(district, 0) + 1

    sorted_districts = sorted(districts.items(), key=lambda item: item[1], reverse=True)
    first_poi = pois[0] if pois else {}
    last_poi = pois[-1] if pois else {}
    middle_poi = pois[len(pois) // 2] if pois else {}

    return {
        "recommendation_basis": "based_on_selected_poi_districts_and_route_order",
        "district_distribution": [
            {"district": district, "poi_count": count}
            for district, count in sorted_districts
        ],
        "primary_area": sorted_districts[0][0] if sorted_districts else None,
        "route_start": {
            "name": first_poi.get("name"),
            "district": first_poi.get("district"),
            "address": first_poi.get("address"),
        },
        "route_middle": {
            "name": middle_poi.get("name"),
            "district": middle_poi.get("district"),
            "address": middle_poi.get("address"),
        },
        "route_end": {
            "name": last_poi.get("name"),
            "district": last_poi.get("district"),
            "address": last_poi.get("address"),
        },
        "selected_pois": [
            {
                "name": poi.get("name"),
                "district": poi.get("district"),
                "address": poi.get("address"),
            }
            for poi in pois
        ],
    }


def _store_pending_options(
    thread_id: str | None,
    route_request: dict[str, Any],
    options: dict[str, Any],
) -> None:
    pois = options.get("pois") or []
    if not thread_id or not pois:
        return

    get_session_store().set_pending_attraction_choices(
        thread_id,
        {
            "city": route_request["city"],
            "days": route_request.get("days") or 1,
            "pace": route_request.get("pace") or "balanced",
            "start_time": route_request.get("start_time") or "09:00",
            "end_time": route_request.get("end_time") or "18:00",
            "pois": pois,
        },
    )


def _attach_planning_preferences(options: dict[str, Any], route_request: dict[str, Any]) -> None:
    options["planning_preferences"] = {
        "days": route_request.get("days") or 1,
        "pace": route_request.get("pace") or "balanced",
        "start_time": route_request.get("start_time") or "09:00",
        "end_time": route_request.get("end_time") or "18:00",
    }


def _resolve_pending_selection(
    message: str,
    thread_id: str | None,
    auto_select_attractions: bool = False,
    auto_fill_remaining_attractions: bool = False,
    additional_attractions: list[str] | None = None,
) -> dict[str, Any] | None:
    if not thread_id:
        return None

    pending = get_session_store().get_pending_attraction_choices(thread_id)
    if not pending:
        return None

    pois = pending.get("pois") or []
    extra_keywords = _normalize_keywords(additional_attractions or [])
    if auto_select_attractions:
        selected_pois = _auto_select_pois(pending, message)
    elif extra_keywords and auto_fill_remaining_attractions:
        selected_pois = _merge_unique_pois(
            _extract_selected_pois(message, pois),
            _auto_select_pois(pending, message),
        )
    elif extra_keywords:
        selected_pois = _extract_selected_pois(message, pois)
    elif _looks_like_selection_message(message, pois):
        selected_pois = _extract_selected_pois(message, pois)
    else:
        return None

    selected_keywords = _merge_keyword_order(
        selected_pois=selected_pois,
        extra_keywords=extra_keywords,
        prefer_extra_first=bool(extra_keywords and auto_fill_remaining_attractions and not _extract_selected_pois(message, pois)),
    )
    if not selected_keywords:
        return None

    get_session_store().clear_pending_attraction_choices(thread_id)
    return {
        "city": pending["city"],
        "days": pending.get("days") or 1,
        "keywords": selected_keywords,
        "max_pois": max(2, len(selected_keywords)),
        "pace": pending.get("pace") or "balanced",
        "start_time": pending.get("start_time") or "09:00",
        "end_time": pending.get("end_time") or "18:00",
        "selected_pois": selected_pois,
        "auto_selected": auto_select_attractions,
        "auto_fill_remaining_attractions": auto_fill_remaining_attractions,
        "additional_attractions": extra_keywords,
    }


def _extract_selected_keywords(message: str, pois: list[dict[str, Any]]) -> list[str]:
    return [poi["name"] for poi in _extract_selected_pois(message, pois) if poi.get("name")]


def _looks_like_selection_message(message: str, pois: list[dict[str, Any]]) -> bool:
    stripped = message.strip()
    if (
        re.search(r"(?:选择|选|挑)\s*(?:\d|[一二三四五六七八九十])", stripped)
        or re.search(r"(?:我要|我想去)\s*(?:\d|[一二三四五六七八九十])", stripped)
    ):
        return True
    if re.fullmatch(r"\s*\d+(?:\s*[、,，和与]\s*\d+)*\s*", stripped):
        return True
    return any(
        poi.get("name") and str(poi["name"]) in message
        for poi in pois
    )


def _auto_select_pois(pending: dict[str, Any], message: str) -> list[dict[str, Any]]:
    pois = pending.get("pois") or []
    if not pois:
        return []

    days = _extract_days_hint(message) if _has_days_hint(message) else pending.get("days") or 1
    candidate_pois = _filter_auto_selectable_pois(pois)
    target_count = _target_auto_poi_count(days, len(candidate_pois))
    ranked_pois = sorted(
        candidate_pois,
        key=lambda poi: (
            _poi_auto_rank(poi),
            len(str(poi.get("name") or "")),
        ),
    )
    selected: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for poi in ranked_pois:
        name = str(poi.get("name") or "").strip()
        if not name or name in seen_names:
            continue
        selected.append(poi)
        seen_names.add(name)
        if len(selected) >= target_count:
            break
    return selected


def _merge_unique_pois(*poi_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for pois in poi_groups:
        for poi in pois:
            name = str(poi.get("name") or "").strip()
            if not name or name in seen_names:
                continue
            merged.append(poi)
            seen_names.add(name)
    return merged


def _merge_keyword_order(
    selected_pois: list[dict[str, Any]],
    extra_keywords: list[str],
    prefer_extra_first: bool,
) -> list[str]:
    selected_keywords = [poi["name"] for poi in selected_pois if poi.get("name")]
    if prefer_extra_first:
        ordered_sources = [extra_keywords, selected_keywords]
    else:
        ordered_sources = [selected_keywords, extra_keywords]

    merged: list[str] = []
    for keywords in ordered_sources:
        for keyword in keywords:
            if keyword and keyword not in merged:
                merged.append(keyword)
    return merged


def _filter_auto_selectable_pois(pois: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = [
        poi
        for poi in pois
        if not _is_auxiliary_poi(poi)
    ]
    return filtered if len(filtered) >= 2 else pois


def _target_auto_poi_count(days: Any, available_count: int) -> int:
    try:
        parsed_days = int(days)
    except (TypeError, ValueError):
        parsed_days = 1
    parsed_days = max(1, min(7, parsed_days))
    if parsed_days <= 1:
        target = 4
    elif parsed_days == 2:
        target = 6
    else:
        target = parsed_days * 3
    return max(2, min(available_count, target))


def _poi_auto_rank(poi: dict[str, Any]) -> int:
    name = str(poi.get("name") or "")
    poi_type = str(poi.get("type") or "")
    address = str(poi.get("address") or "")
    text = f"{name} {poi_type} {address}"
    score = 100
    if any(keyword in text for keyword in ("风景名胜", "景区", "省级景点", "旅游景点")):
        score -= 30
    if any(keyword in text for keyword in ("博物馆", "纪念馆", "陈列馆", "文化", "古城", "老城", "公园")):
        score -= 20
    if any(keyword in text for keyword in ("旧址", "历史", "街", "广场")):
        score -= 12
    if any(keyword in text for keyword in ("相关", "停车场", "售票", "入口", "出口", "游客中心", "卫生间")):
        score += 40
    return score


def _is_auxiliary_poi(poi: dict[str, Any]) -> bool:
    text = f"{poi.get('name') or ''} {poi.get('type') or ''} {poi.get('address') or ''}"
    return any(keyword in text for keyword in ("停车场", "售票", "入口", "出口", "游客中心", "卫生间"))


def _extract_selected_pois(message: str, pois: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[str] = []
    selected_pois: list[dict[str, Any]] = []

    selection_text = _extract_selection_text(message)
    indexes = [int(value) for value in re.findall(r"\d+", selection_text)]
    for index in indexes:
        if 1 <= index <= len(pois):
            poi = pois[index - 1]
            name = poi.get("name")
            if name and name not in selected:
                selected.append(name)
                selected_pois.append(poi)

    for poi in pois:
        name = poi.get("name")
        if name and name in message and name not in selected:
            selected.append(name)
            selected_pois.append(poi)

    return selected_pois


def _extract_selection_text(message: str) -> str:
    if "选择" not in message:
        return message

    selection_text = message.split("选择", 1)[1]
    stop_patterns = ["改成", "安排", "每天", "节奏", "轻松", "紧凑", "宽松", "从", "到"]
    stop_positions = [
        position
        for pattern in stop_patterns
        if (position := selection_text.find(pattern)) >= 0
    ]
    if stop_positions:
        selection_text = selection_text[: min(stop_positions)]
    return selection_text


def _format_attraction_options(options: dict[str, Any]) -> str:
    if options.get("error"):
        return f"获取景点候选失败：{options['error']}"

    city_name = (options.get("matched_city") or {}).get("name") or options.get("city")
    pois = options.get("pois") or []
    preferences = options.get("planning_preferences") or {}
    lines = [f"我先为你查到了{city_name}的候选景点，请选择想去的景点后我再规划路线：", ""]

    if preferences:
        pace_text = {
            "compact": "紧凑",
            "relaxed": "轻松",
            "balanced": "适中",
        }.get(preferences.get("pace"), "适中")
        lines.extend(
            [
                f"当前规划偏好：{preferences.get('days', 1)}天，节奏{pace_text}，"
                f"每天 {preferences.get('start_time', '09:00')} - {preferences.get('end_time', '18:00')}。",
                "如果要调整，可以在选择景点时一起说明。",
                "",
            ]
        )

    for index, poi in enumerate(pois, start=1):
        address = poi.get("address") or "暂无地址"
        poi_type = poi.get("type") or "景点"
        lines.append(f"{index}. {poi.get('name')} - {poi_type} - {address}")

    lines.extend(
        [
            "",
            "你可以回复编号，例如：选择 1、3、5",
            "也可以直接回复景点名称，例如：我想去开元寺、西街、清源山",
            "如果候选列表里没有你想去的景点，也可以直接补充名称，例如：选择 1、3，另外我还想去南澳岛",
            "如果不想自己选择，也可以回复：你帮我选并直接规划",
            "也可以补充规划偏好，例如：选择 1、3、5，安排2天，轻松一点，每天9点开始18点结束",
        ]
    )
    return "\n".join(lines)


def _extract_city_hint(message: str) -> str | None:
    patterns = [
        r"(?:规划|安排|设计|做)?[1-7一二两三四五六七]\s*天(?:的)?(?P<city>[\u4e00-\u9fff]{2,8}?)(?:旅游|旅行|路线|行程|游玩)",
        r"(?:规划|安排|设计|做)?[1-7一二两三四五六七]\s*天(?P<city>[\u4e00-\u9fff]{2,8}?)(?:的)?(?:旅游|旅行|路线|行程|游玩)",
        r"(?P<city>[\u4e00-\u9fff]{2,8}?)[1-7一二两三四五六七]\s*天(?:的)?(?:旅游|旅行|路线|行程|游玩)?",
        r"(?:规划|安排|设计|做)(?P<city>[\u4e00-\u9fff]{2,8}?)(?:一日游|二日游|三日游|[1-7一二两三四五六七]天|旅游|旅行|路线|行程)",
        r"(?:去|到|在)(?P<city>[\u4e00-\u9fff]{2,8}?)(?:旅游|旅行|玩|一日游|二日游|三日游|路线|行程)",
        r"(?P<city>[\u4e00-\u9fff]{2,8}?)(?:一日游|二日游|三日游|[1-7一二两三四五六七]天|旅游路线|旅行路线|游玩路线|行程)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            city = _clean_text(match.group("city"))
            if city:
                return city
    return None


def _extract_days_hint(message: str) -> int:
    special = {
        "一日游": 1,
        "二日游": 2,
        "两日游": 2,
        "三日游": 3,
    }
    for keyword, value in special.items():
        if keyword in message:
            return value

    match = re.search(r"([1-7一二两三四五六七])\s*天", message)
    if not match:
        return 1
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7}
    value = match.group(1)
    if value in digits:
        return digits[value]
    return int(value)


def _extract_pace_hint(message: str) -> str:
    if any(keyword in message for keyword in ("紧凑", "多玩", "特种兵", "充实")):
        return "compact"
    if any(keyword in message for keyword in ("轻松", "悠闲", "慢一点", "不累", "宽松")):
        return "relaxed"
    return "balanced"


def _extract_start_time_hint(message: str) -> str | None:
    patterns = [
        r"(?:从|每天|早上|上午)?\s*(\d{1,2})[:：点](\d{2})?\s*(?:开始|出发|起)",
        r"(\d{1,2})\s*点\s*(?:开始|出发|起)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
            return _format_time(hour, minute)
    return None


def _extract_end_time_hint(message: str) -> str | None:
    patterns = [
        r"(?:到|至|晚上|下午)?\s*(\d{1,2})[:：点](\d{2})?\s*(?:结束|截止|回来|返程)",
        r"(\d{1,2})\s*点\s*(?:结束|截止|回来|返程)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
            if "下午" in match.group(0) or "晚上" in match.group(0):
                hour = _to_24_hour(hour)
            return _format_time(hour, minute)
    return None


def _merge_route_preferences(route_request: dict[str, Any], message: str) -> dict[str, Any]:
    merged = dict(route_request)
    if days := _extract_days_hint(message):
        if _has_days_hint(message):
            merged["days"] = days
    if pace := _extract_pace_hint(message):
        if pace != "balanced" or not merged.get("pace"):
            merged["pace"] = pace
    if start_time := _extract_start_time_hint(message):
        merged["start_time"] = start_time
    if end_time := _extract_end_time_hint(message):
        merged["end_time"] = end_time
    merged.setdefault("pace", "balanced")
    merged.setdefault("start_time", "09:00")
    merged.setdefault("end_time", "18:00")
    return merged


def _normalize_pace(value: Any) -> str:
    text = str(value or "").lower()
    if text in {"compact", "紧凑", "充实"}:
        return "compact"
    if text in {"relaxed", "轻松", "悠闲", "宽松"}:
        return "relaxed"
    return "balanced"


def _has_days_hint(message: str) -> bool:
    return any(keyword in message for keyword in ("一日游", "二日游", "两日游", "三日游")) or bool(
        re.search(r"[1-7一二两三四五六七]\s*天", message)
    )


def _format_time(hour: int, minute: int) -> str:
    hour = max(0, min(23, hour))
    minute = max(0, min(59, minute))
    return f"{hour:02d}:{minute:02d}"


def _to_24_hour(hour: int) -> int:
    if 1 <= hour <= 11:
        return hour + 12
    return hour


def _extract_keywords_hint(message: str) -> list[str]:
    markers = ["想去", "包括", "包含", "必须去", "重点去"]
    for marker in markers:
        if marker in message:
            tail = message.split(marker, 1)[1]
            tail = re.split(r"[。！？?]", tail, maxsplit=1)[0]
            tail = re.split(
                r"(?:，|,)?(?:轻松|悠闲|宽松|紧凑|充实|不累|安排|每天|从|到|改成)",
                tail,
                maxsplit=1,
            )[0]
            return _normalize_keywords(re.split(r"[、,，和与/ ]+", tail))
    return []


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


def _normalize_keywords(values: list[Any]) -> list[str]:
    keywords: list[str] = []
    for value in values:
        keyword = _clean_text(str(value))
        if keyword and keyword not in keywords:
            keywords.append(keyword)
    return keywords[:8]


def _clean_text(value: str) -> str:
    cleaned = value.strip(" ，,。！？?的路线行程")
    if cleaned in {"帮我", "帮我安排", "帮我规划", "安排", "规划", "设计", "做"}:
        return ""
    if any(keyword in cleaned for keyword in ("帮我", "安排", "规划", "设计")):
        return ""
    if len(cleaned) < 2:
        return ""
    return cleaned


def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return minimum
    return max(minimum, min(maximum, parsed))
