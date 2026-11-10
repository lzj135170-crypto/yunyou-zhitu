"""AMap POI MCP 冒烟测试：stdio 启动 + list_tools + search_poi 成功/空结果。

不会启动 FastAPI，也不写数据库；只验证 MCP server 的 POI tool。
需要能访问高德 POI 接口。没配 `AMAP_WEB_SERVICE_KEY` 时打印 SKIP 并返回 0。

用法（在 backend 目录下执行）：
    .venv/Scripts/python.exe scripts/smoke_mcp_amap.py
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.config import settings

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
    if not settings.AMAP_MAP_ENABLED or not settings.AMAP_WEB_SERVICE_KEY.strip():
        print("SKIP：未配置高德 Web 服务 key 或 AMAP_MAP_ENABLED=false")
        return 0

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", settings.MCP_WEATHER_MODULE],
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
                "list_tools 包含 search_poi",
                "search_poi" in names,
                str(sorted(names)),
            )

            result = await session.call_tool(
                "search_poi",
                {
                    "city": "成都",
                    "keywords": "景点",
                    "types": "110000",
                    "limit": 3,
                },
            )
            payload = _payload(result)
            check("search_poi 成功", result.isError is False and payload.get("ok") is True)
            items = payload.get("items") or []
            check("search_poi 返回地点", bool(items), str(payload)[:300])
            if items:
                first = items[0]
                check(
                    "地点字段齐全",
                    bool(first.get("name"))
                    and isinstance(first.get("longitude"), (int, float))
                    and isinstance(first.get("latitude"), (int, float)),
                    str(first)[:300],
                )
                check(
                    "地点城市匹配请求",
                    str(first.get("cityname") or "").startswith("成都"),
                    str(first.get("cityname")),
                )

            bad = await session.call_tool(
                "search_poi",
                {
                    "city": "不存在的城市XYZ",
                    "keywords": "景点",
                    "types": "110000",
                    "limit": 3,
                },
            )
            bad_payload = _payload(bad)
            check(
                "不存在的城市返回业务错误",
                bad.isError is False
                and bad_payload.get("ok") is False
                and bad_payload.get("error_code")
                in {"NO_RESULT", "AMAP_UPSTREAM_UNAVAILABLE"},
                str(bad_payload)[:300],
            )

            invalid = await session.call_tool(
                "search_poi",
                {"city": "", "keywords": "景点"},
            )
            invalid_payload = _payload(invalid)
            check(
                "空城市返回 INVALID_ARGUMENT",
                invalid.isError is False
                and invalid_payload.get("error_code") == "INVALID_ARGUMENT",
                str(invalid_payload)[:300],
            )

    print(f"\n结果：{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
