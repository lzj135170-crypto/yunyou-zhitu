"""Travel facts MCP server：天气（Open-Meteo）+ 高德 POI 事实。

由 app/services/mcp_weather.py 通过 stdio 拉起。
天气工具：geocode_city、get_forecast、get_weather（组合前两者）。
POI 工具：search_poi，返回真实名称/地址/类型/评分/参考人均。

两者共用一个进程，复用 stdio 桥接；POI 调用量低，暂不拆分。
stdout 是 MCP 协议通道，本文件不调用 print，日志写 stderr。
"""

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from typing import Any

import anyio
import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from app.services.weather_client import (
    HTTP_TIMEOUT,
    MAX_FORECAST_DAYS,
    fetch_forecast,
    fetch_place,
)

logger = logging.getLogger(__name__)

# host/port 只对 streamable-http 有意义；stdio 模式不会用到。
mcp = FastMCP("travel-facts", host="127.0.0.1", port=8765)

# 和 amap_map.POI_MAX_LIMIT 保持一致。这里单独定义是为了让参数校验
# 不依赖对 amap_map 的延迟 import（那个 import 会连带加载 settings）。
POI_MAX_LIMIT = 10


def _result(payload: dict[str, Any]) -> CallToolResult:
    """同时返回结构化 payload 和文本内容。

    isError=False：业务错误不是协议错误；真正的执行异常由 FastMCP 标记。
    """
    return CallToolResult(
        content=[
            TextContent(
                type="text",
                text=json.dumps(payload, ensure_ascii=False),
            )
        ],
        structuredContent=payload,
        isError=False,
    )


def _ok(**fields: Any) -> CallToolResult:
    return _result({"ok": True, **fields})


def _error(code: str, message: str) -> CallToolResult:
    return _result({"ok": False, "error_code": code, "message": message})


def _parse_start_date(raw: str) -> date | None:
    if not raw or raw.strip().upper() == "UNKNOWN":
        return date.today()
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


def _window(start: date, days: int) -> tuple[date, date] | None:
    """校验并返回日期窗口；超出 Open-Meteo 的 16 天窗口返回 None。"""
    today = date.today()
    last_available = today + timedelta(days=MAX_FORECAST_DAYS - 1)
    if days < 1 or days > MAX_FORECAST_DAYS:
        return None
    if start < today or start > last_available:
        return None
    end = min(start + timedelta(days=days - 1), last_available)
    return start, end


def _geocode_sync(city: str) -> tuple[float, float] | None:
    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        return fetch_place(client, city)


def _forecast_sync(
    latitude: float, longitude: float, start: date, end: date
) -> list[dict[str, Any]] | None:
    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        return fetch_forecast(client, latitude, longitude, start, end)


def _weather_sync(
    city: str, start: date, end: date
) -> tuple[str, dict[str, Any]]:
    """组合 geocode + forecast。

    返回 (status, payload)：
      - OK：payload 含 days
      - CITY_NOT_FOUND：Open-Meteo 正常返回但没有这个地点
      - UPSTREAM_UNAVAILABLE：网络/接口失败
    """
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            coords = fetch_place(client, city)
            if coords is None:
                return "CITY_NOT_FOUND", {}
            days_data = fetch_forecast(client, coords[0], coords[1], start, end)
            if not days_data:
                return "UPSTREAM_UNAVAILABLE", {}
            return "OK", {
                "city": city,
                "latitude": coords[0],
                "longitude": coords[1],
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "days": days_data,
            }
    except Exception as e:
        # 只记类型，不把可能含完整 URL 的异常原文打出来。
        logger.warning("Open-Meteo 取数失败：%s", type(e).__name__)
        return "UPSTREAM_UNAVAILABLE", {}


@mcp.tool()
async def geocode_city(city: str) -> CallToolResult:
    """查询中文城市名对应的经纬度。找不到时 error_code=CITY_NOT_FOUND。"""
    city = (city or "").strip()
    if not city:
        return _error("INVALID_ARGUMENT", "city 不能为空")

    try:
        coords = await anyio.to_thread.run_sync(_geocode_sync, city)
    except Exception as e:
        logger.warning("geocode_city 上游失败：%s", type(e).__name__)
        return _error("UPSTREAM_UNAVAILABLE", "天气地理编码接口暂时不可用")

    if coords is None:
        return _error("CITY_NOT_FOUND", f"没有查到「{city}」这个地点")
    return _ok(city=city, latitude=coords[0], longitude=coords[1])


@mcp.tool()
async def get_forecast(
    latitude: float,
    longitude: float,
    start_date: str = "",
    days: int = 3,
) -> CallToolResult:
    """查询指定坐标、指定日期窗口的逐日预报（最多 16 天）。

    start_date 留空表示从今天开始；超出预报窗口返回 error_code=OUT_OF_RANGE。
    """
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return _error("INVALID_ARGUMENT", "经纬度超出合法范围")

    start = _parse_start_date(start_date)
    if start is None:
        return _error("OUT_OF_RANGE", "start_date 必须是 YYYY-MM-DD")

    window = _window(start, days)
    if window is None:
        return _error(
            "OUT_OF_RANGE",
            f"日期窗口超出 Open-Meteo 的 {MAX_FORECAST_DAYS} 天预报范围",
        )

    window_start, window_end = window
    try:
        days_data = await anyio.to_thread.run_sync(
            _forecast_sync, latitude, longitude, window_start, window_end
        )
    except Exception as e:
        logger.warning("get_forecast 上游失败：%s", type(e).__name__)
        return _error("UPSTREAM_UNAVAILABLE", "天气接口暂时不可用")

    if not days_data:
        return _error("UPSTREAM_UNAVAILABLE", "天气接口没有返回可用数据")

    return _ok(
        latitude=latitude,
        longitude=longitude,
        start_date=window_start.isoformat(),
        end_date=window_end.isoformat(),
        days=days_data,
    )


@mcp.tool()
async def get_weather(
    city: str,
    start_date: str = "",
    days: int = 3,
) -> CallToolResult:
    """按中文城市名一次取回日期窗口内的逐日预报（geocode + forecast）。"""
    city = (city or "").strip()
    if not city:
        return _error("INVALID_ARGUMENT", "city 不能为空")

    start = _parse_start_date(start_date)
    if start is None:
        return _error("OUT_OF_RANGE", "start_date 必须是 YYYY-MM-DD")

    window = _window(start, days)
    if window is None:
        return _error(
            "OUT_OF_RANGE",
            f"日期窗口超出 Open-Meteo 的 {MAX_FORECAST_DAYS} 天预报范围",
        )

    window_start, window_end = window
    try:
        status, payload = await anyio.to_thread.run_sync(
            _weather_sync, city, window_start, window_end
        )
    except Exception as e:
        logger.warning("get_weather 上游失败：%s", type(e).__name__)
        return _error("UPSTREAM_UNAVAILABLE", "天气接口暂时不可用")

    if status == "CITY_NOT_FOUND":
        return _error("CITY_NOT_FOUND", f"没有查到「{city}」这个地点")
    if status != "OK":
        return _error("UPSTREAM_UNAVAILABLE", "天气接口没有返回可用数据")

    return _ok(**payload)


@mcp.tool()
async def search_poi(
    city: str,
    keywords: str,
    types: str = "",
    limit: int = 8,
) -> CallToolResult:
    """按城市 + 关键词查高德真实 POI（名称/地址/类型/评分/参考人均）。

    `types` 可传高德大类码（餐饮 050000）；不传则按关键词搜。
    高德不返回门票和营业时间，调用方不能把 `avg_cost` 当报价。
    """
    city = (city or "").strip()
    keywords = (keywords or "").strip()
    types = (types or "").strip()
    if not city or not keywords:
        return _error("INVALID_ARGUMENT", "city 和 keywords 不能为空")
    if not 1 <= limit <= POI_MAX_LIMIT:
        return _error("INVALID_ARGUMENT", f"limit 只能是 1—{POI_MAX_LIMIT}")

    try:
        # 延迟 import：amap_map 会 import settings，而 Settings 要求
        # DATABASE_URL / JWT_SECRET_KEY。放在模块顶层会让天气 server
        # 也依赖数据库配置；这里只在真正调用 POI 时才加载。
        from app.services import amap_map
    except Exception as e:
        logger.warning("POI 检索模块不可用：%s", type(e).__name__)
        return _error("AMAP_NOT_CONFIGURED", "高德 POI 模块不可用")

    def _search():
        return amap_map.search_pois(
            keywords, city=city, types=types, limit=limit
        )

    try:
        items = await anyio.to_thread.run_sync(_search)
    except amap_map.AmapNotConfigured:
        return _error("AMAP_NOT_CONFIGURED", "未配置高德 Web 服务 key，POI 检索不可用")
    except amap_map.AmapUpstreamError as e:
        # 只记类型，不打异常原文：上游错误里可能带 key/完整 URL。
        logger.warning("高德 POI 检索失败：%s", type(e).__name__)
        return _error("AMAP_UPSTREAM_UNAVAILABLE", "高德 POI 检索暂时不可用")
    except Exception as e:
        logger.warning("POI 检索失败：%s", type(e).__name__)
        return _error("AMAP_UPSTREAM_UNAVAILABLE", "POI 检索暂时不可用")

    if not items:
        return _error("NO_RESULT", "没有找到匹配的地点")

    return _ok(
        city=city,
        keywords=keywords,
        source="amap",
        items=[
            {
                "name": item.name,
                "address": item.address,
                "cityname": item.cityname,
                "adname": item.adname,
                "type": item.type,
                "typecode": item.typecode,
                "longitude": item.longitude,
                "latitude": item.latitude,
                "tel": item.tel,
                "rating": item.rating,
                "business_area": item.business_area,
                "avg_cost": item.avg_cost,
            }
            for item in items
        ],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Travel facts MCP server (weather + AMap POI)"
    )
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default="stdio",
        help="stdio 给后端子进程用；streamable-http 给 MCP Inspector 用",
    )
    args = parser.parse_args(argv)

    # 只写 stderr；stdout 留给 MCP 协议。
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    mcp.run(transport=args.transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
