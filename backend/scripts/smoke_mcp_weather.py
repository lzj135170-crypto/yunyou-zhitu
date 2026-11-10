"""Weather MCP server 冒烟测试：stdio 启动 + list_tools + 三个 tool 的成功/错误路径。

不会启动 FastAPI，也不写数据库；只验证 MCP server 本身。
需要能访问 Open-Meteo（geocode/forecast 两条真实 HTTP）。

用法（在 backend 目录下执行）：
    .venv/Scripts/python.exe scripts/smoke_mcp_weather.py
"""

import asyncio
import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

BACKEND_DIR = Path(__file__).resolve().parent.parent
_passed = 0
_failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    if ok:
        _passed += 1
        print(f"  OK  {label}")
    else:
        _failed += 1
        print(f"  FAIL {label}")
        if detail:
            print(f"       {detail}")


def _payload(result) -> dict:
    if result.structuredContent is not None:
        payload = result.structuredContent
    else:
        text = "".join(
            block.text
            for block in (result.content or [])
            if getattr(block, "type", "") == "text"
        )
        payload = json.loads(text)
    if isinstance(payload, dict) and set(payload) == {"result"}:
        payload = payload["result"]
    return payload


async def main() -> int:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_servers.weather_server"],
        cwd=str(BACKEND_DIR),
        env=env,
        encoding="utf-8",
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            check(
                "list_tools 包含三个工具",
                {"geocode_city", "get_forecast", "get_weather"} <= names,
                str(sorted(names)),
            )

            geocode = await session.call_tool(
                "geocode_city", {"city": "成都"}
            )
            geo = _payload(geocode)
            check("geocode_city 成功", geocode.isError is False and geo.get("ok") is True)
            check(
                "geocode_city 返回经纬度",
                isinstance(geo.get("latitude"), (int, float))
                and isinstance(geo.get("longitude"), (int, float)),
                str(geo),
            )

            today = date.today().isoformat()
            weather = await session.call_tool(
                "get_weather",
                {"city": "成都", "start_date": today, "days": 3},
            )
            data = _payload(weather)
            days = data.get("days") or []
            check("get_weather 成功", weather.isError is False and data.get("ok") is True)
            check("get_weather 返回 3 天", len(days) == 3, str(len(days)))
            if days:
                first = days[0]
                check(
                    "天气字段齐全",
                    {"date", "weather", "temp_max", "temp_min", "precip_prob"}
                    <= set(first),
                    str(first),
                )
                check("天气日期匹配", first.get("date") == today, str(first))

            missing = await session.call_tool(
                "get_weather",
                {"city": "不存在的城市名XYZ", "start_date": today, "days": 1},
            )
            missing_payload = _payload(missing)
            check(
                "不存在的城市返回 CITY_NOT_FOUND",
                missing.isError is False
                and missing_payload.get("ok") is False
                and missing_payload.get("error_code") == "CITY_NOT_FOUND",
                str(missing_payload),
            )

            out_of_range = await session.call_tool(
                "get_weather",
                {"city": "成都", "start_date": today, "days": 99},
            )
            range_payload = _payload(out_of_range)
            check(
                "超过 16 天返回 OUT_OF_RANGE",
                out_of_range.isError is False
                and range_payload.get("ok") is False
                and range_payload.get("error_code") == "OUT_OF_RANGE",
                str(range_payload),
            )

    print(f"\n结果：{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
