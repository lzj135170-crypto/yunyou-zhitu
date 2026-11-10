"""验证 POI MCP 的降级语义和 mcp:amap 熔断策略。

不启动 MCP 子进程，也不打高德：monkeypatch `mcp_weather.search_pois`，
直接检查 `_poi.fetch_poi` 在各种失败下是否只留 meta、不抛异常。
最后单独验证 `mcp:amap` 熔断器的三态转换。

用法（在 backend 目录下执行）：
    .venv/Scripts/python.exe scripts/verify_mcp_amap_degradation.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.nodes import _poi
from app.common.resilience import CircuitOpenError, get_breaker
from app.config import settings
from app.services import mcp_weather
from app.services.mcp_weather import MCPUnavailableError

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

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


def _state(city: str = "成都", query: str = "成都三日游", findings=None) -> dict:
    if findings is None:
        findings = {"rag": {"filters": {"city": city}}}
    return {"user_query": query, "messages": [], "findings": findings}


def main() -> int:
    original_search = mcp_weather.search_pois
    original_mcp = settings.MCP_ENABLED
    original_amap = settings.MCP_AMAP_ENABLED
    settings.MCP_ENABLED = True
    settings.MCP_AMAP_ENABLED = True
    request = _poi.PoiRequest(keywords="景点", types="110000", limit=3)

    try:
        # ---- 1. 成功：块和 meta 都要有 ----
        mcp_weather.search_pois = lambda *a, **k: {
            "ok": True,
            "items": [
                {"name": "宽窄巷子", "address": "青羊区长顺上街", "rating": 4.6},
                {"name": "人民公园", "address": "青羊区少城路", "rating": 4.7},
            ],
        }
        block, meta = _poi.fetch_poi(_state(), "attraction", request)
        check("成功时返回 POI 块", "宽窄巷子" in block and "人民公园" in block, block[:200])
        check("成功时带事实来源", meta.get("source") == "amap")
        check("成功时记录条数", meta.get("count") == 2)

        # ---- 2. 业务错误：不抛，meta 带错误码 ----
        mcp_weather.search_pois = lambda *a, **k: {
            "ok": False,
            "error_code": "AMAP_NOT_CONFIGURED",
            "message": "no key",
        }
        block, meta = _poi.fetch_poi(_state(), "food", request)
        check("业务错误不抛异常", block == "")
        check("业务错误 meta 带错误码", meta.get("error") == "AMAP_NOT_CONFIGURED")
        check("业务错误 meta 带城市", meta.get("city") == "成都")

        # ---- 3. MCP 不可用：不抛，meta 标 mcp_unavailable ----
        def raise_unavailable(*a, **k):
            raise MCPUnavailableError("server down")

        mcp_weather.search_pois = raise_unavailable
        block, meta = _poi.fetch_poi(_state(), "hotel", request)
        check("MCP 不可用不抛异常", block == "")
        check("MCP 不可用 meta", meta.get("error") == "mcp_unavailable")

        # ---- 4. 熔断中：不抛，meta 标 circuit_open ----
        def raise_open(*a, **k):
            raise CircuitOpenError("mcp:amap")

        mcp_weather.search_pois = raise_open
        block, meta = _poi.fetch_poi(_state(), "attraction", request)
        check("熔断中不抛异常", block == "")
        check("熔断中 meta", meta.get("error") == "circuit_open")

        # ---- 5. 开关关闭：完全不调用 ----
        settings.MCP_AMAP_ENABLED = False
        called = False

        def should_not_call(*a, **k):
            nonlocal called
            called = True
            return {"ok": True, "items": []}

        mcp_weather.search_pois = should_not_call
        block, meta = _poi.fetch_poi(_state(), "attraction", request)
        check("关闭时返回空块", block == "")
        check("关闭时 meta enabled=false", meta.get("enabled") is False)
        check("关闭时不调用 MCP", called is False)

        # ---- 6. 区域目的地：跳过，不拿某个城市冒充整个区域 ----
        settings.MCP_AMAP_ENABLED = True
        mcp_weather.search_pois = lambda *a, **k: {
            "ok": True,
            "items": [{"name": "不该出现"}],
        }
        block, meta = _poi.fetch_poi(_state(city="云南"), "attraction", request)
        check("区域目的地跳过 POI", block == "")
        check("区域目的地 meta", meta.get("skipped") == "region")

        # ---- 7. 城市回退：rag 没给城市时从用户原话里找 ----
        seen = {}

        def capture(city, keywords, types, limit):
            seen.update({"city": city, "keywords": keywords, "types": types})
            return {"ok": True, "items": [{"name": "宽窄巷子"}]}

        mcp_weather.search_pois = capture
        _poi.fetch_poi(_state(findings={}), "attraction", request)
        check("从用户原话解析城市", seen.get("city") == "成都", str(seen))
        check("关键词带城市前缀", str(seen.get("keywords", "")).startswith("成都 "), str(seen))
        check("类型码透传", seen.get("types") == "110000")

        # ---- 8. 没有城市：不查，meta 标 NO_CITY ----
        block, meta = _poi.fetch_poi(
            _state(city="", query="你好", findings={}), "attraction", request
        )
        check("没有城市时不查", block == "")
        check("没有城市 meta", meta.get("error") == "NO_CITY")

        # ---- 9. mcp:amap 熔断：连续失败 3 次后拒绝 ----
        breaker = get_breaker("mcp:amap")

        def fail():
            raise RuntimeError("upstream failed")

        for _ in range(3):
            try:
                breaker.call(fail)
            except RuntimeError:
                pass

        try:
            breaker.call(fail)
            check("mcp:amap 熔断后拒绝", False, "第 4 次仍然执行了")
        except CircuitOpenError:
            check("mcp:amap 熔断后拒绝", True)

    finally:
        mcp_weather.search_pois = original_search
        settings.MCP_ENABLED = original_mcp
        settings.MCP_AMAP_ENABLED = original_amap

    print(f"\n结果：{_passed} passed, {_failed} failed")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
