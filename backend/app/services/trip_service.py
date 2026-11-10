"""把智能体图产出投影成结果页的 TripPlanResponse。

只读取最近一次成功运行，安全提取字段并过滤内部内容；不调用模型。
"""

import json
import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.common import cache
from app.common.exceptions import BusinessException
from app.config import settings
from app.models.agent_run import AgentRun
from app.models.conversation import Conversation
from app.models.message import Message
from app.schemas.trip import (
    TripAttraction,
    TripDay,
    TripFood,
    TripHotel,
    TripPlanResponse,
    TripSource,
    TripTransport,
    TripWeather,
    TripWeatherDay,
)
from app.services.conversation_service import get_owned_conversation

# 结果页顶部概述最多展示多少字。超过就按句号截断——
# 最终回复动辄上千字，原样塞进卡片会把整页撑得没法看。
OVERVIEW_MAX_CHARS = 220

_EXPERT_LABELS = {
    "rag": "知识库",
    "attraction": "景点",
    "food": "美食",
    "hotel": "住宿",
    "transport": "交通",
    "weather": "天气",
    "itinerary": "行程",
    "live": "实时数据",
}

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def get_latest_plan(
    db: Session, conversation_id: int, user_id: int
) -> TripPlanResponse:
    """返回会话最近一次成功运行对应的结果页数据。

    找不到成功运行才抛 404。运行成功但没有 findings（例如模型只回了一句话）
    仍然返回一个只带最终回复的 plan：前端可以展示回答并继续调整，
    这比让历史列表点进来看到一片空白好。
    """
    conversation = get_owned_conversation(db, conversation_id, user_id)

    # 先用缓存挡掉重复的只读投影；权限校验必须在读缓存之前，
    # 否则换一个用户拿同一个会话 id 也能命中别人的行程。
    key = cache.plan_key(conversation_id)
    cached = cache.get_json(key)
    if cached is not None:
        try:
            return TripPlanResponse.model_validate(cached)
        except (ValidationError, TypeError, ValueError):
            # 旧版本结构或损坏载荷按未命中处理，继续查数据库。
            cache.delete(key)

    # ⚠️ 不能无脑取"最近一次成功运行"。
    #
    # 用户在结果页问一句"谢谢"或"那天气呢"，图仍然会成功跑完，
    # 但这一轮不一定产出新的按天行程。直接取最新一次，会把之前
    # 完整的行程覆盖成一张空卡片——看起来像"聊了一句行程就没了"。
    #
    # 所以优先取最近一次【带按天行程】的运行；都没有时才退回最新一次。
    # 只多读几行 JSONB，不调模型。
    candidates = (
        db.query(AgentRun)
        .filter(
            AgentRun.conversation_id == conversation_id,
            AgentRun.status == "success",
        )
        .order_by(AgentRun.id.desc())
        .limit(10)
        .all()
    )
    if not candidates:
        raise BusinessException(message="这个会话还没有生成行程", code=404)

    run = next((item for item in candidates if _has_itinerary(item.output)), candidates[0])

    output = run.output if isinstance(run.output, dict) else {}
    findings = output.get("findings")
    if not isinstance(findings, dict):
        findings = {}

    # 追问可能只跑部分节点（例如“住宿换成高档型”只产出 hotel + itinerary）。
    # 最新运行缺少的板块，用最近的、真正有数据的运行补齐；
    # 否则一次局部追问会把已经生成的地图、天气、景点和交通全部冲掉。
    findings = dict(findings)
    for section in ("weather", "attraction", "transport", "hotel", "food", "rag"):
        if _section_has_data(section, findings.get(section)):
            continue
        for other in candidates:
            if other.id == run.id:
                continue
            other_findings = (other.output or {}).get("findings")
            if not isinstance(other_findings, dict):
                continue
            value = other_findings.get(section)
            if _section_has_data(section, value):
                findings[section] = value
                break

    user_message = _user_message_for_run(db, conversation_id, run)
    assistant_message = _assistant_message_for_run(db, conversation_id, run)
    plan = build_plan_for_run(
        db,
        conversation,
        run,
        user_message=user_message,
        assistant_message=assistant_message,
        findings=findings,
    )
    cache.set_json(
        key,
        plan.model_dump(mode="json"),
        settings.RESULT_CACHE_TTL_SECONDS,
    )
    return plan


def build_plan_for_run(
    db: Session,
    conversation: Conversation,
    run: AgentRun,
    *,
    user_message: Message | None = None,
    assistant_message: Message | None = None,
    findings: dict[str, Any] | None = None,
) -> TripPlanResponse:
    """把一次运行投影成结果页响应。

    单独抽出来是为了让“结果页读取”和“行程规范化落库”共用同一套字段映射：
    两边各写一遍迟早会漂移，最后同一个 run 在 /plan 和 trips.plan_snapshot 里不一致。
    """
    if findings is None:
        output = run.output if isinstance(run.output, dict) else {}
        raw_findings = output.get("findings")
        findings = raw_findings if isinstance(raw_findings, dict) else {}

    if user_message is None:
        user_message = _user_message_for_run(db, conversation.id, run)
    if assistant_message is None:
        assistant_message = _assistant_message_for_run(db, conversation.id, run)

    answer = assistant_message.content if assistant_message is not None else ""
    request_meta = _request_meta(user_message)

    destination = _destination(findings)
    days = _days(findings)
    weather = _weather(findings)

    return TripPlanResponse(
        conversation_id=conversation.id,
        title=conversation.title or "未命名行程",
        destination=destination,
        overview=_overview(answer, destination, days),
        answer=answer,
        start_date=request_meta["start_date"],
        end_date=request_meta["end_date"],
        travelers=request_meta["travelers"],
        budget=request_meta["budget"],
        pace=request_meta["pace"],
        hotel_level=request_meta["hotel_level"],
        days=days,
        attractions=_attractions(findings),
        foods=_foods(findings),
        food_note=_clean_multiline(_get(findings, "food", "dining_tip")),
        hotels=_hotels(findings),
        transport=_transport(findings),
        weather=weather,
        budget_assumption=_safe_str(_get(findings, "hotel", "budget_assumption")),
        tips=_tips(findings),
        warnings=_warnings(findings),
        sources=_sources(findings),
        generated_at=assistant_message.create_time if assistant_message is not None else None,
    )


# ==========================================================================
# 运行记录 / 消息
# ==========================================================================


def _user_message_for_run(
    db: Session, conversation_id: int, run: AgentRun
) -> Message | None:
    """取这次运行对应的用户消息。

    优先用 run.request_message_id（迁移后回填的强关联），
    老数据没有这个值时再退回 input.message_id。
    """
    message_id = run.request_message_id
    if message_id is None and isinstance(run.input, dict):
        candidate = run.input.get("message_id")
        if isinstance(candidate, int):
            message_id = candidate

    query = db.query(Message).filter(
        Message.conversation_id == conversation_id,
        Message.role == "user",
    )
    if message_id is not None:
        query = query.filter(Message.id == message_id)
    return query.order_by(Message.id.desc()).first()


def _request_meta(message: Message | None) -> dict[str, Any]:
    """从规划页生成的提示词里解析回日期/人数/预算等表单字段。

    前端 `composeTripPrompt()` 用的是固定格式，所以这里能稳定解析；
    用户自由输入的消息解析不到时全部留空，前端会退回 localStorage。
    这不是"从自然语言里猜"——只认那几行固定前缀。
    """
    empty = {
        "start_date": "",
        "end_date": "",
        "travelers": None,
        "budget": None,
        "pace": "",
        "hotel_level": "",
    }
    if message is None or not isinstance(message.content, str):
        return empty

    text = message.content
    start_date = end_date = ""
    date_match = re.search(
        r"日期[：:]\s*(\d{4}-\d{2}-\d{2})\s*至\s*(\d{4}-\d{2}-\d{2})", text
    )
    if date_match:
        start_date, end_date = date_match.group(1), date_match.group(2)

    travelers_match = re.search(r"人数[：:]\s*(\d+)\s*人", text)
    budget_match = re.search(r"预算[：:]\s*约?\s*(\d+)\s*元", text)
    pace_match = re.search(r"节奏[：:]\s*([^\n]+)", text)
    hotel_match = re.search(r"住宿偏好[：:]\s*([^\n]+)", text)

    return {
        "start_date": start_date,
        "end_date": end_date,
        "travelers": int(travelers_match.group(1)) if travelers_match else None,
        "budget": int(budget_match.group(1)) if budget_match else None,
        "pace": pace_match.group(1).strip() if pace_match else "",
        "hotel_level": hotel_match.group(1).strip() if hotel_match else "",
    }


def _assistant_message_for_run(
    db: Session, conversation_id: int, run: AgentRun
) -> Message | None:
    """找这次运行产出的那条 AI 回复。

    优先用 run.response_message_id（迁移后回填的强关联）。
    老数据没有这个值时，退回“id 大于用户消息的第一条 assistant”，
    这样即使会话里还有更晚的失败运行，也不会把别的回复配给这次运行。
    """
    if run.response_message_id is not None:
        message = (
            db.query(Message)
            .filter(
                Message.id == run.response_message_id,
                Message.conversation_id == conversation_id,
                Message.role == "assistant",
            )
            .first()
        )
        if message is not None:
            return message

    user_message_id = run.request_message_id
    if user_message_id is None and isinstance(run.input, dict):
        candidate = run.input.get("message_id")
        if isinstance(candidate, int):
            user_message_id = candidate

    query = db.query(Message).filter(
        Message.conversation_id == conversation_id,
        Message.role == "assistant",
    )
    if user_message_id is not None:
        query = query.filter(Message.id > user_message_id)
    message = query.order_by(Message.id.asc()).first()

    if message is None:
        # 兜底：老数据里 input 可能没有 message_id。取最新一条 assistant，
        # 至少保证结果页有内容可展示。
        message = (
            db.query(Message)
            .filter(
                Message.conversation_id == conversation_id,
                Message.role == "assistant",
            )
            .order_by(Message.id.desc())
            .first()
        )
    return message


# ==========================================================================
# findings → 结果页字段
# ==========================================================================


def _get(findings: dict[str, Any], *path: str) -> Any:
    """按路径取嵌套字段，任何一层不是 dict 都返回 None。"""
    current: Any = findings
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _has_itinerary(output: Any) -> bool:
    """这次运行有没有产出按天行程。用来避免一句闲聊覆盖掉完整行程。"""
    if not isinstance(output, dict):
        return False
    findings = output.get("findings")
    if not isinstance(findings, dict):
        return False
    days = _get(findings, "itinerary", "days")
    return isinstance(days, list) and bool(days)


def _section_has_data(section: str, value: Any) -> bool:
    """判断某个 findings 板块是否有可展示内容。

    追问的节点产出经常是空壳（例如 attraction 存在但 attractions 为空），
    所以不能只看 key 是否存在。
    """
    if not isinstance(value, dict):
        return False
    if section == "weather":
        return bool(_safe_str(value.get("city")).strip() or value.get("days"))
    if section == "attraction":
        return bool(value.get("attractions"))
    if section == "transport":
        return bool(value.get("options"))
    if section == "hotel":
        return bool(
            value.get("hotels")
            or _safe_str(value.get("budget_assumption")).strip()
        )
    if section == "food":
        return bool(
            value.get("foods") or _safe_str(value.get("dining_tip")).strip()
        )
    if section == "rag":
        # 结果页的“参考来源”只读 citations；有 hits 但没有引用时，
        # 说明这一轮没带回可展示的出处，允许退回最近的完整运行。
        return bool(value.get("citations"))
    return bool(value)


def _destination(findings: dict[str, Any]) -> str:
    """目的地优先取天气节点解析出的城市，其次取 RAG 的过滤条件。

    不从会话标题里猜：标题可能被用户改过、也可能是"帮我规划一下"这种
    （首条消息自动生成），拿它当目的地会把错误信息显示在地图卡片上。
    """
    for path in (("weather", "city"), ("rag", "filters", "city")):
        value = _safe_str(_get(findings, *path)).strip()
        if value:
            return value
    return ""


def _days(findings: dict[str, Any]) -> list[TripDay]:
    raw = _get(findings, "itinerary", "days")
    if not isinstance(raw, list):
        return []

    out: list[TripDay] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        day_number = _safe_int(item.get("day"), default=index)
        out.append(
            TripDay(
                day=day_number,
                morning=_safe_str(item.get("morning")),
                afternoon=_safe_str(item.get("afternoon")),
                evening=_safe_str(item.get("evening")),
            )
        )
    return out


def _attractions(findings: dict[str, Any]) -> list[TripAttraction]:
    raw = _get(findings, "attraction", "attractions")
    if not isinstance(raw, list):
        return []

    out: list[TripAttraction] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = _safe_str(item.get("name")).strip()
        if not name:
            continue
        out.append(
            TripAttraction(
                name=name,
                reason=_safe_str(item.get("reason")),
                hours=_safe_float(item.get("hours")),
                price_range=_safe_str(item.get("price_range")),
            )
        )
    return out


def _clean_multiline(value: Any) -> str:
    """模型偶尔把换行写成字面量 \\n（JSON 双重转义），展示前还原。"""
    return _safe_str(value).replace("\\n", "\n").strip()


def _foods(findings: dict[str, Any]) -> list[TripFood]:
    raw = _get(findings, "food", "foods")
    if not isinstance(raw, list):
        return []

    out: list[TripFood] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = _safe_str(item.get("name")).strip()
        if not name:
            continue
        out.append(
            TripFood(
                name=name,
                area=_safe_str(item.get("area")),
                category=_safe_str(item.get("category")),
                reason=_safe_str(item.get("reason")),
                price_range=_safe_str(item.get("price_range")),
                meal_time=_safe_str(item.get("meal_time")),
            )
        )
    return out


def _hotels(findings: dict[str, Any]) -> list[TripHotel]:
    raw = _get(findings, "hotel", "hotels")
    if not isinstance(raw, list):
        return []

    out: list[TripHotel] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        area = _safe_str(item.get("area")).strip()
        if not area:
            continue
        out.append(
            TripHotel(
                area=area,
                level=_safe_str(item.get("level")),
                reason=_safe_str(item.get("reason")),
                suitable_for=_safe_str(item.get("suitable_for")),
                price_range=_safe_str(item.get("price_range")),
            )
        )
    return out


def _transport(findings: dict[str, Any]) -> list[TripTransport]:
    raw = _get(findings, "transport", "options")
    if not isinstance(raw, list):
        return []

    out: list[TripTransport] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        mode = _safe_str(item.get("mode")).strip()
        if not mode:
            continue
        out.append(
            TripTransport(
                mode=mode,
                duration=_safe_str(item.get("duration")),
                best_for=_safe_str(item.get("best_for")),
                price_range=_safe_str(item.get("price_range")),
                note=_safe_str(item.get("note")),
            )
        )
    return out


def _weather(findings: dict[str, Any]) -> TripWeather | None:
    raw = _get(findings, "weather")
    if not isinstance(raw, dict):
        return None

    days_raw = raw.get("days")
    days: list[TripWeatherDay] = []
    if isinstance(days_raw, list):
        for item in days_raw:
            if not isinstance(item, dict):
                continue
            days.append(
                TripWeatherDay(
                    date=_safe_str(item.get("date")),
                    weather=_safe_str(item.get("weather")),
                    temp_max=_safe_float(item.get("temp_max")),
                    temp_min=_safe_float(item.get("temp_min")),
                    precip_prob=_safe_float(item.get("precip_prob")),
                )
            )

    weather = TripWeather(
        city=_safe_str(raw.get("city")),
        days=days,
        summary=_safe_str(raw.get("summary")),
        advice=_safe_str(raw.get("advice")),
        source=_safe_str(raw.get("source")),
        error=_safe_str(raw.get("error")),
    )

    # 什么都没有的 weather 节点（LLM 没配、节点直接失败）不要占一张卡片。
    if not (weather.city or weather.days or weather.summary or weather.advice or weather.error):
        return None
    return weather


def _meaningful_tip(text: str) -> bool:
    """过滤模型输出里的空壳/JSON 残片。

    itinerary.notes 偶尔会返回 "':['" 这种截断片段；直接展示会让
    “旅行提示”出现一条无意义的符号。这里要求至少两个文字或数字。
    """
    if text.strip().lower() in {"none", "null", "nil", "undefined"}:
        return False
    return len(re.sub(r"[\W_]+", "", text, flags=re.UNICODE)) >= 2


def _tips(findings: dict[str, Any]) -> list[str]:
    candidates = [
        _get(findings, "itinerary", "notes"),
        _get(findings, "transport", "trip_note"),
    ]
    out: list[str] = []
    for value in candidates:
        text = _clean_multiline(value)
        for piece in re.split(r"\n+", text):
            tip = piece.strip(" \t-•·")
            if not tip or tip in out or not _meaningful_tip(tip):
                continue
            out.append(tip)
    return out


def _warnings(findings: dict[str, Any]) -> list[str]:
    """把各专家的 error 翻成人能看懂的一句话。

    不把完整异常栈给前端：里面可能有数据库连接串或本机路径。
    """
    out: list[str] = []
    for name, finding in findings.items():
        if not isinstance(finding, dict):
            continue
        error = _safe_str(finding.get("error")).strip()
        if not error:
            continue
        label = _EXPERT_LABELS.get(name, name)
        text = f"{label}：{error}"
        if text not in out:
            out.append(text)
    return out


def _sources(findings: dict[str, Any]) -> list[TripSource]:
    raw = _get(findings, "rag", "citations")
    if not isinstance(raw, list):
        return []

    out: list[TripSource] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = _safe_str(item.get("title")).strip()
        if not title:
            continue
        source = item.get("source")
        source_text = source if isinstance(source, str) and source.strip() else None
        key = (title, source_text or "")
        if key in seen:
            continue
        seen.add(key)
        section = item.get("section")
        out.append(
            TripSource(
                title=title,
                source=source_text,
                section=[str(s) for s in section if str(s).strip()]
                if isinstance(section, list)
                else [],
            )
        )
    return out[:12]


# ==========================================================================
# 概述
# ==========================================================================


def _overview(answer: str, destination: str, days: list[TripDay]) -> str:
    """从最终回复里截一段适合放在顶部卡片的概述。

    不额外调模型：结果页刷新是只读操作，不应该产生 token 成本。
    截断优先落在句号/问号后面，避免把一句话切成半截。

    ⚠️ 这里要先把 Markdown 记号去掉。最终回复是给聊天窗口写的，
    里面常有 `**Day 1**`、`###`、`- ` 这类标记；原样放进卡片会显示成
    一堆星号。完整回复卡片仍然保留原文，只有顶部概述做纯文本化。
    """
    text = " ".join(_plain_markdown(answer).split())
    if not text:
        if destination and days:
            return f"已为 {destination} 生成 {len(days)} 天行程，下面是每天安排和实用信息。"
        if destination:
            return f"已生成 {destination} 的旅行建议。"
        return "已生成旅行建议，下面是整理后的行程和实用信息。"

    if len(text) <= OVERVIEW_MAX_CHARS:
        return text

    clipped = text[:OVERVIEW_MAX_CHARS]
    boundary = max(clipped.rfind("。"), clipped.rfind("！"), clipped.rfind("？"))
    if boundary >= 80:
        return clipped[: boundary + 1]
    return clipped.rstrip() + "…"


# ==========================================================================
# 类型归一化
# ==========================================================================


def _plain_markdown(text: str) -> str:
    """去掉最常见的 Markdown 记号，供顶部概述使用。

    只处理行内粗体、代码、标题和列表前缀。不追求完整的 Markdown 解析：
    结果页的完整回复仍然原样展示，这里的目标只是让摘要别出现星号。
    """
    if not text:
        return ""
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"(?m)^\s*#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "", text)
    return text


def _safe_str(value: Any) -> str:
    """把模型可能返回的任意值变成字符串。

    model 偶尔会把一个字段写成列表（比如把 price_range 写成 ["50", "80"]），
    直接 str() 会得到 Python 列表字面量，前端看起来很奇怪；
    列表统一用顿号拼接。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "、".join(_safe_str(v) for v in value if v is not None)
    if isinstance(value, dict):
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return ""
    return str(value)


def _safe_float(value: Any) -> float | None:
    """取数字；带单位的字符串（"2小时"）就提取其中的数字。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = _NUMBER_RE.search(value)
        if match:
            try:
                return float(match.group())
            except ValueError:
                return None
    return None


def _safe_int(value: Any, default: int) -> int:
    number = _safe_float(value)
    if number is None:
        return default
    return int(number)
