"""Weather Agent：天气查询 + 穿衣和行程建议。

流程：模型解析城市和日期 → HTTP 取真实预报 → 模型生成建议。
城市和日期从多轮对话中解析，日期解析失败宁可查不到，也不拿今天顶替。
"""

import logging
import time
from datetime import date, timedelta
from typing import Any, Literal

import httpx
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.agents.llm import invoke_structured
from app.agents.nodes._common import finish_expert, recent_history
from app.agents.prompts import WEATHER_PLACE_PROMPT, WEATHER_PROMPT
from app.agents.state import TravelState
from app.common.resilience import CircuitOpenError
from app.config import settings
from app.services import mcp_weather
from app.services.weather_client import (
    HTTP_TIMEOUT,
    MAX_FORECAST_DAYS,
    fetch_forecast,
    fetch_place,
)

logger = logging.getLogger(__name__)

# 超过 7 天的预报准确度明显下降，必须提醒用户可能变化。
FAR_OUT_DAYS = 7


class PlaceQuery(BaseModel):
    """第一步的产出：用户到底想问哪里的天气、哪几天。"""

    city: str = Field(
        description="城市名，用中文。用户没有明说、历史里也推断不出来时，填 UNKNOWN",
    )
    days: int = Field(default=3, description="要查几天，默认 3")
    start_date: str = Field(
        default="UNKNOWN",
        description=(
            "出行开始日期，格式 YYYY-MM-DD（今天的日期见指令）。"
            "用户没说日期、或从历史里也推断不出来时填 UNKNOWN。"
            "⚠️ 不要因为'用户没提日期'就默认填今天——那等于替他改成了另一个问题"
        ),
    )


def _parse_start_date(raw: str | None) -> date | None:
    """解析出行日期；失败返回 None，不猜日期，避免把另一段天气当成用户问的。"""
    if not raw or raw.strip().upper() == "UNKNOWN":
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        logger.warning("出行日期解析失败，按「没提日期」处理：%r", raw)
        return None


class WeatherAdvice(BaseModel):
    """第三步的产出：基于真实数据给出的建议。"""

    summary: str = Field(description="这两天天气怎么样，一段话说清楚")
    advice: str = Field(description="穿衣和行程安排建议，要具体，结合天气数据")


def weather_node(state: TravelState) -> Command[Literal["planner"]]:
    """天气节点。

    三种无数据情况（未识别城市、地理编码失败、上游失败）都在 findings 里
    写明原因，绝不编造预报；下游据此知道天气为空。
    """
    name = "weather"
    tokens = 0
    today = date.today()

    # ---- 1. 解析目的地和日期 ----
    try:
        place, t1 = invoke_structured(
            WEATHER_PLACE_PROMPT,
            recent_history(state),
            instruction=f"今天是 {today.isoformat()}。用户想问哪个城市、哪几天的天气？",
            schema=PlaceQuery,
            node="weather_place",
        )
        tokens += t1
    except Exception as e:
        logger.warning("%s 解析目的地失败：%s", name, e)
        return finish_expert(name, {"error": f"解析目的地失败：{e}"}, tokens)

    if place is None or not place.city or place.city.strip().upper() == "UNKNOWN":
        return finish_expert(
            name,
            {"error": "没能从对话里确定要查哪个城市的天气，请先告诉我目的地"},
            tokens,
        )

    city = place.city.strip()
    # 夹到合法区间：模型偶尔会给 0 或 100 这种数，直接透传给接口会 400。
    days = max(1, min(int(place.days or 3), MAX_FORECAST_DAYS))

    # 窗口末端。用 MAX_FORECAST_DAYS - 1 算，不要手写 +16（见上面的实测记录）。
    last_available = today + timedelta(days=MAX_FORECAST_DAYS - 1)
    start = _parse_start_date(place.start_date)

    if start is not None and start < today:
        # 提示词已经说了过去的日期要填 UNKNOWN，但模型偶尔还是会填。
        # 真去查上周的天气再当成"出行预报"讲出去，比查不到更糟。
        logger.warning("出行日期在过去（%s），按「没提日期」处理", start)
        start = None

    # 出行日期整个落在预报窗口之外：如实说查不到，而不是拿今天的天气顶上。
    # 这是本节点最要紧的一条边界——日期错了，温度和降水看着都很正常，
    # 用户没有任何办法发现自己看的是不相干的那几天。
    if start is not None and start > last_available:
        gap = (start - today).days
        return finish_expert(
            name,
            {
                "city": city,
                "trip_start": start.isoformat(),
                "days_until_trip": gap,
                "error": (
                    f"用户问的是 {start.isoformat()}（距今 {gap} 天），超出了能查的范围。"
                    f"预报最远只到 {last_available.isoformat()}（16 天以内），"
                    f"再远的没有可靠数据，硬给一段出来也是编的。"
                    f"应告诉用户出发前一周左右再查一次，那时候的预报才有参考价值。"
                ),
            },
            tokens,
        )

    # 没提日期就按今天起算（用户问"那边天气怎么样"，要的就是近几天）；
    # 提了日期就查他说的那一段。末端截到 last_available，
    # 免得行程跨过 16 天边界时接口直接 400。
    window_start = start or today
    window_end = min(window_start + timedelta(days=days - 1), last_available)

    # ---- 2. 取真实数据 ----
    # MCP 是优先路径；MCP_ENABLED=false 时完全不启动客户端，行为与以前一致。
    source = "Open-Meteo（真实预报）"
    mcp_fallback = False
    mcp_latency_ms: int | None = None
    days_data: list[dict[str, Any]] | None = None

    if settings.MCP_ENABLED:
        window_days = (window_end - window_start).days + 1
        started = time.perf_counter()
        try:
            payload = mcp_weather.get_weather(
                city, window_start.isoformat(), window_days
            )
        except (mcp_weather.MCPUnavailableError, CircuitOpenError) as e:
            logger.warning("MCP 天气取数失败，回退直连：%s", type(e).__name__)
            mcp_fallback = True
            if not settings.MCP_FALLBACK_DIRECT:
                return finish_expert(
                    name,
                    {"city": city, "error": "天气服务暂时不可用，请稍后重试"},
                    tokens,
                )
        except Exception as e:
            # MCP 客户端本身有 bug/未预期异常时也不能把整轮对话打崩；
            # 和 transport 失败一样，退回原来的直连路径。
            logger.warning("MCP 天气调用异常，回退直连：%s", type(e).__name__)
            mcp_fallback = True
            if not settings.MCP_FALLBACK_DIRECT:
                return finish_expert(
                    name,
                    {"city": city, "error": "天气服务暂时不可用，请稍后重试"},
                    tokens,
                )
        else:
            mcp_latency_ms = int((time.perf_counter() - started) * 1000)
            if not isinstance(payload, dict):
                payload = {}
            if payload.get("ok"):
                raw_days = payload.get("days")
                days_data = raw_days if isinstance(raw_days, list) else []
                if days_data:
                    source = "Open-Meteo（MCP）"
                else:
                    logger.warning("MCP 返回空天气数据，回退直连")
                    mcp_fallback = True
                    # 空列表会让后面的 `if days_data is None` 误以为
                    # "已经有结果"，所以显式置 None 才会进入直连分支。
                    days_data = None
                    if not settings.MCP_FALLBACK_DIRECT:
                        return finish_expert(
                            name,
                            {"city": city, "error": "天气接口没有返回可用数据"},
                            tokens,
                        )
            else:
                error_code = str(payload.get("error_code") or "")
                message = str(payload.get("message") or "")
                if error_code == "CITY_NOT_FOUND":
                    return finish_expert(
                        name,
                        {
                            "city": city,
                            "error": f"没有查到「{city}」这个地点，换个说法再试试（比如加上省份）",
                        },
                        tokens,
                    )
                if error_code == "OUT_OF_RANGE":
                    return finish_expert(
                        name,
                        {"city": city, "error": message or "日期超出了天气预报范围"},
                        tokens,
                    )
                # UPSTREAM_UNAVAILABLE 等业务错误：MCP server 是活的，
                # 只是它访问 Open-Meteo 失败；允许回退直连再试一次。
                logger.warning(
                    "MCP 天气返回业务错误 %s，回退直连",
                    error_code or "UNKNOWN",
                )
                mcp_fallback = True
                if not settings.MCP_FALLBACK_DIRECT:
                    return finish_expert(
                        name,
                        {
                            "city": city,
                            "error": (
                                f"天气接口不可用："
                                f"{message or error_code or '未知错误'}"
                            ),
                        },
                        tokens,
                    )

    if days_data is None:
        try:
            with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
                coords = fetch_place(client, city)
                if coords is None:
                    return finish_expert(
                        name,
                        {
                            "city": city,
                            "error": f"没有查到「{city}」这个地点，换个说法再试试（比如加上省份）",
                        },
                        tokens,
                    )
                days_data = fetch_forecast(
                    client, coords[0], coords[1], window_start, window_end
                )
        except Exception as e:
            logger.warning("%s 请求天气接口失败：%s", name, e)
            return finish_expert(name, {"city": city, "error": f"天气接口不可用：{e}"}, tokens)

    if not days_data:
        return finish_expert(
            name, {"city": city, "error": "天气接口没有返回可用数据"}, tokens
        )

    # ---- 3. 基于真实数据生成建议 ----
    # 注意这一步【失败不算整体失败】：数据已经拿到了，只是没生成出建议文案。
    # 所以把原始数据照样放进产出，让 response 节点能直接用。
    finding: dict[str, Any] = {
        "city": city,
        "days": days_data,
        "source": source,
    }
    if source == "Open-Meteo（MCP）":
        finding["mcp_tool"] = "get_weather"
        if mcp_latency_ms is not None:
            finding["mcp_latency_ms"] = mcp_latency_ms
    elif mcp_fallback:
        finding["mcp_fallback"] = True
        if mcp_latency_ms is not None:
            finding["mcp_latency_ms"] = mcp_latency_ms

    # 把"距今还有几天"一并交给下游：这个数决定要不要提醒用户"预报会变"。
    # 在 Python 里算而不是让模型自己数，是因为它必须准——
    # 模型数错日期不会报错，只会安静地少说或多说一句提醒，谁也发现不了。
    far_out_hint = ""
    if start is not None:
        gap = (start - today).days
        finding["trip_start"] = start.isoformat()
        finding["days_until_trip"] = gap
        if gap >= FAR_OUT_DAYS:
            far_out_hint = (
                f"\n⚠️ 出行日期距今还有 {gap} 天，远期预报的准确度会明显下降。"
                f"必须在建议里明确提醒用户：这只是参考，出发前要再确认一次。"
            )

    try:
        advice, t2 = invoke_structured(
            WEATHER_PROMPT,
            recent_history(state),
            instruction=(
                f"今天是 {today.isoformat()}。以下是 {city} "
                f"{window_start.isoformat()} 到 {window_end.isoformat()} 的"
                f"真实天气预报数据：\n{days_data}\n\n"
                f"请据此给出天气总结和穿衣、行程建议。{far_out_hint}"
            ),
            schema=WeatherAdvice,
            node="weather_advice",
        )
        tokens += t2
    except Exception as e:
        logger.warning("%s 生成建议失败：%s", name, e)
        advice = None

    if advice is not None:
        finding["summary"] = advice.summary
        finding["advice"] = advice.advice
    else:
        finding["note"] = "建议文案生成失败，但上面的预报数据是真实的"

    return finish_expert(name, finding, tokens)
