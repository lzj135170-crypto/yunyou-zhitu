"""Open-Meteo 纯 HTTP 客户端，不依赖 LangGraph/LangChain/FastAPI。

只负责请求和把 daily 数组转成按天列表；日期窗口和业务判断在调用方。
"""

import logging
from datetime import date
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# 5 秒。天气是锦上添花的信息，为它把整轮对话（本来就要 20 多秒）再拖长不值得。
# 超时就如实说"天气没查到"，其余专家的结论照常返回。
HTTP_TIMEOUT = 5.0

# 含今天共 16 天，最远到 today + 15；窗口末端统一用 MAX_FORECAST_DAYS - 1。
MAX_FORECAST_DAYS = 16

# WMO 天气代码 → 中文，避免把数字码直接展示给用户。
WMO_TEXT: dict[int, str] = {
    0: "晴",
    1: "大体晴朗",
    2: "局部多云",
    3: "阴",
    45: "有雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "大毛毛雨",
    56: "冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "大阵雪",
    95: "雷阵雨",
    96: "雷阵雨伴小冰雹",
    99: "雷阵雨伴大冰雹",
}


def describe_weather(code: Any) -> str:
    """把天气码翻成中文。

    落回"未知天气(码)"而不是抛异常或猜一个：真出现没收录的码时，
    user 看到的是一个诚实的"不知道"，而不是一句编出来的"多云"。
    """
    try:
        return WMO_TEXT.get(int(code), f"未知天气({code})")
    except (TypeError, ValueError):
        return f"未知天气({code})"


def fetch_place(client: httpx.Client, city: str) -> tuple[float, float] | None:
    """查询城市经纬度。

    返回 None 只表示接口正常但没有该地点；网络异常必须抛出，
    避免把服务故障误报成用户写错地名。
    """
    resp = client.get(
        GEOCODE_URL,
        params={"name": city, "count": 1, "language": "zh", "format": "json"},
    )
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        return None
    top = results[0]
    return float(top["latitude"]), float(top["longitude"])


def fetch_forecast(
    client: httpx.Client, latitude: float, longitude: float, start: date, end: date
) -> list[dict[str, Any]] | None:
    """获取 start~end 的逐日预报并转成中文；失败返回 None。

    必须同时传 start_date 和 end_date，只传一个会返回 400。
    """
    try:
        resp = client.get(
            FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "Asia/Shanghai",
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
            },
        )
        resp.raise_for_status()
        daily = resp.json().get("daily") or {}
    except Exception as e:
        logger.warning("天气接口请求失败：%s", e)
        return None

    dates = daily.get("time") or []
    if not dates:
        return None

    # 接口返回的是"按字段分列的数组"（每种数据一个数组），
    # 这里转成"按天组织的列表"——按字段的格式直接喂给模型，它很容易把
    # 第 2 天的低温和第 3 天的降水概率串到同一天去。
    out: list[dict[str, Any]] = []
    for i, day in enumerate(dates):
        out.append(
            {
                "date": day,
                "weather": describe_weather((daily.get("weather_code") or [None])[i]),
                "temp_max": (daily.get("temperature_2m_max") or [None])[i],
                "temp_min": (daily.get("temperature_2m_min") or [None])[i],
                "precip_prob": (daily.get("precipitation_probability_max") or [None])[i],
            }
        )
    return out
