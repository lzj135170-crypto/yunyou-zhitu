"""专家节点的高德 POI 事实注入。

景点 / 美食 / 住宿专家在调模型前查一次高德 POI，把真实的名称、地址、
类型、评分和参考人均写进指令。RAG 提供静态知识，POI 提供实时存在性。

POI 只是增强：未配置 key、MCP 未开启、子进程不可用或城市解析失败时，
只返回空块和 meta，专家照常运行，任何失败都不抛出节点。
POI 不返回门票和营业时间，`avg_cost` 只是参考人均，不能当报价。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage

from app.agents.state import TravelState
from app.common.resilience import CircuitOpenError
from app.config import settings
from app.rag.policy import CITIES
from app.services import mcp_weather
from app.services.mcp_weather import MCPUnavailableError

logger = logging.getLogger(__name__)

# 高德 region + city_limit 对省级区域不稳定，宁可跳过，也不拿单城 POI 冒充整个区域。
_REGION_DESTINATIONS = frozenset({"云南", "川西"})


@dataclass(frozen=True)
class PoiRequest:
    """某个专家想查哪类 POI。`keywords` 不带城市，由 fetch_poi 补。"""

    keywords: str
    types: str = ""
    limit: int = 8


def fetch_poi(
    state: TravelState, expert: str, request: PoiRequest
) -> tuple[str, dict[str, Any]]:
    """取 POI 事实，返回 (提示词块, 排查用 meta)。

    meta 挂在 `findings[expert]["poi"]`，记录是否用上及原因；
    `items` 同时是生成后校验器的事实源。
    """
    if not (settings.MCP_ENABLED and settings.MCP_AMAP_ENABLED):
        return "", {"enabled": False}

    city = _find_city(state)
    if not city:
        return "", {"enabled": True, "error": "NO_CITY"}
    if city in _REGION_DESTINATIONS:
        return "", {"enabled": True, "skipped": "region", "city": city}

    try:
        payload = mcp_weather.search_pois(
            city,
            f"{city} {request.keywords}",
            request.types,
            request.limit,
        )
    except CircuitOpenError:
        logger.warning("%s 专家 POI 检索熔断中", expert)
        return "", {"enabled": True, "error": "circuit_open", "city": city}
    except MCPUnavailableError:
        logger.warning("%s 专家 POI 服务不可用", expert)
        return "", {"enabled": True, "error": "mcp_unavailable", "city": city}
    except Exception as e:
        logger.warning("%s 专家 POI 检索失败：%s", expert, type(e).__name__)
        return "", {"enabled": True, "error": "unexpected", "city": city}

    if not isinstance(payload, dict) or not payload.get("ok"):
        if isinstance(payload, dict):
            error_code = str(payload.get("error_code") or "")
            message = str(payload.get("message") or "")
        else:
            error_code = ""
            message = ""
        return "", {
            "enabled": True,
            "error": error_code or "bad_payload",
            "city": city,
            "message": message,
        }

    items = payload.get("items")
    if not isinstance(items, list):
        return "", {"enabled": True, "error": "bad_payload", "city": city}

    clean = [
        item
        for item in items
        if isinstance(item, dict) and str(item.get("name") or "").strip()
    ]
    if not clean:
        return "", {"enabled": True, "error": "NO_RESULT", "city": city}

    return _format_block(city, clean), {
        "enabled": True,
        "source": "amap",
        "city": city,
        "count": len(clean),
        "items": clean,
    }


def _find_city(state: TravelState) -> str:
    """按可靠性依次从检索过滤、天气结果、用户原话中解析城市。

    只扫人类消息，不扫 AI 回复，否则上一轮模型编出的城市会被继续当成事实。
    匹配时城市优先于区域。
    """
    findings = state.get("findings") or {}

    rag = findings.get("rag") or {}
    filters = rag.get("filters") or {}
    city = str(filters.get("city") or "").strip()
    if city:
        return city

    weather = findings.get("weather") or {}
    city = str(weather.get("city") or "").strip()
    if city:
        return city

    text = str(state.get("user_query") or "")
    for message in state.get("messages") or []:
        if isinstance(message, HumanMessage):
            text += "\n" + str(message.content)

    for name in sorted(CITIES, key=len, reverse=True):
        if name in _REGION_DESTINATIONS:
            continue
        if name in text:
            return name
    for name in _REGION_DESTINATIONS:
        if name in text:
            return name
    return ""


def _format_block(city: str, items: list[dict[str, Any]]) -> str:
    """把 POI 条目格式化为给模型读的事实块。

    只保留推荐需要的字段；坐标、电话、商圈不进提示词，避免模型输出无关信息。
    """
    lines = [
        "【高德 POI 事实】",
        f"下面是高德实时返回的「{city}」真实地点。名称、地址、类型、评分、参考人均可以直接使用；",
        "这里没有的票价、营业时间仍按估算处理，不要写成确定事实。",
    ]

    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue

        extras: list[str] = []
        address = str(item.get("address") or "").strip()
        if address:
            extras.append(f"地址：{address}")
        poi_type = str(item.get("type") or "").strip()
        if poi_type:
            extras.append(f"类型：{poi_type}")
        rating = item.get("rating")
        if rating is not None and rating != "":
            extras.append(f"评分：{rating}")
        avg_cost = str(item.get("avg_cost") or "").strip()
        if avg_cost:
            extras.append(f"参考人均：{avg_cost}元")

        suffix = f"（{'；'.join(extras)}）" if extras else ""
        lines.append(f"- {name}{suffix}")

    return "\n".join(lines)
