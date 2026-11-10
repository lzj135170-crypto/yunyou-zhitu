"""验证天气节点在 MCP 开启/失败/业务错误时的降级路径。

不启动 MCP server、不访问网络：
- 天气节点的 LLM 调用和直连 HTTP 都用假函数替换；
- MCP 客户端用假 payload / 假异常替换；
- 只验证节点在不同结果下产出的 findings、是否回退直连、是否标记 mcp_fallback。

用法（在 backend 目录下执行）：
    .venv/Scripts/python.exe scripts/verify_mcp_degradation.py
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.nodes import weather
from app.common.resilience import get_breaker, reset_all
from app.config import settings
from app.services import mcp_weather

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


def _fake_invoke(
    _prompt, _history, instruction=None, schema=None, node="", **_kwargs
):
    if schema is weather.PlaceQuery:
        return weather.PlaceQuery(city="成都", days=3, start_date="UNKNOWN"), 1
    if schema is weather.WeatherAdvice:
        return weather.WeatherAdvice(summary="晴", advice="正常出行"), 2
    return None, 0


def _run_node(*, mcp_enabled, mcp_impl=None, fallback_direct=True):
    """跑一次 weather_node，返回 (finding, done, tokens, calls)。"""
    original_invoke = weather.invoke_structured
    original_place = weather.fetch_place
    original_forecast = weather.fetch_forecast
    original_mcp = weather.mcp_weather.get_weather
    original_enabled = settings.MCP_ENABLED
    original_fallback = settings.MCP_FALLBACK_DIRECT
    calls = {"direct": 0, "mcp": 0}

    def fake_place(_client, _city):
        calls["direct"] += 1
        return 30.0, 104.0

    def fake_forecast(_client, _lat, _lon, start, _end):
        calls["direct"] += 1
        return [
            {
                "date": start.isoformat(),
                "weather": "晴",
                "temp_max": 20,
                "temp_min": 12,
                "precip_prob": 0,
            }
        ]

    def fake_mcp(*args, **kwargs):
        calls["mcp"] += 1
        if mcp_impl is None:
            raise AssertionError("MCP_ENABLED=false 时不应调用 MCP")
        return mcp_impl(*args, **kwargs)

    settings.MCP_ENABLED = mcp_enabled
    settings.MCP_FALLBACK_DIRECT = fallback_direct
    weather.invoke_structured = _fake_invoke
    weather.fetch_place = fake_place
    weather.fetch_forecast = fake_forecast
    weather.mcp_weather.get_weather = fake_mcp
    try:
        command = weather.weather_node(
            {"messages": [], "user_query": "成都未来三天天气"}
        )
        update = command.update
        return (
            update["findings"]["weather"],
            update.get("done"),
            update.get("tokens", 0),
            calls,
        )
    finally:
        weather.invoke_structured = original_invoke
        weather.fetch_place = original_place
        weather.fetch_forecast = original_forecast
        weather.mcp_weather.get_weather = original_mcp
        settings.MCP_ENABLED = original_enabled
        settings.MCP_FALLBACK_DIRECT = original_fallback


def test_mcp_disabled() -> None:
    print("\n[1] MCP_ENABLED=false：保持原有直连")
    finding, done, tokens, calls = _run_node(mcp_enabled=False)
    check("走直连", finding.get("source") == "Open-Meteo（真实预报）", str(finding))
    check("没有 mcp_fallback", "mcp_fallback" not in finding, str(finding))
    check("没有调用 MCP", calls["mcp"] == 0, str(calls))
    check("直连调用了 geocode + forecast", calls["direct"] == 2, str(calls))
    check("done 包含 weather", done == ["weather"], str(done))
    check("token 汇总", tokens == 3, str(tokens))


def test_mcp_success() -> None:
    print("\n[2] MCP 成功：source 标 MCP，不回退直连")
    day = {
        "date": date.today().isoformat(),
        "weather": "晴",
        "temp_max": 20,
        "temp_min": 12,
        "precip_prob": 0,
    }
    finding, done, tokens, calls = _run_node(
        mcp_enabled=True,
        mcp_impl=lambda *_a, **_k: {"ok": True, "days": [day]},
    )
    check("source=Open-Meteo（MCP）", finding.get("source") == "Open-Meteo（MCP）", str(finding))
    check("带 mcp_tool", finding.get("mcp_tool") == "get_weather", str(finding))
    check(
        "带 mcp_latency_ms",
        isinstance(finding.get("mcp_latency_ms"), int),
        str(finding),
    )
    check("没有 mcp_fallback", "mcp_fallback" not in finding, str(finding))
    check("没有走直连", calls["direct"] == 0, str(calls))
    check("调用了 MCP", calls["mcp"] == 1, str(calls))
    check("done 包含 weather", done == ["weather"], str(done))
    check("token 汇总", tokens == 3, str(tokens))


def test_mcp_unavailable_fallback() -> None:
    print("\n[3] MCP 不可用 + 允许回退：走直连并标 mcp_fallback")
    def down(*_a, **_k):
        raise mcp_weather.MCPUnavailableError("模拟 MCP 挂了")

    finding, done, tokens, calls = _run_node(
        mcp_enabled=True, mcp_impl=down, fallback_direct=True
    )
    check("回退到直连 source", finding.get("source") == "Open-Meteo（真实预报）", str(finding))
    check("标记 mcp_fallback", finding.get("mcp_fallback") is True, str(finding))
    check("直连被调用", calls["direct"] == 2, str(calls))
    check("done 包含 weather", done == ["weather"], str(done))
    check("token 汇总", tokens == 3, str(tokens))


def test_mcp_unavailable_no_fallback() -> None:
    print("\n[4] MCP 不可用 + 禁止回退：返回错误，不调用直连")
    def down(*_a, **_k):
        raise mcp_weather.MCPUnavailableError("模拟 MCP 挂了")

    finding, done, tokens, calls = _run_node(
        mcp_enabled=True, mcp_impl=down, fallback_direct=False
    )
    check("finding 有 error", bool(finding.get("error")), str(finding))
    check("错误文案是天气服务不可用", "天气服务暂时不可用" in finding["error"], str(finding))
    check("没有走直连", calls["direct"] == 0, str(calls))
    check("done 包含 weather", done == ["weather"], str(done))


def test_business_errors_do_not_fallback() -> None:
    print("\n[5] MCP 业务错误：CITY_NOT_FOUND / OUT_OF_RANGE 不回退直连")
    city_missing, done1, _, calls1 = _run_node(
        mcp_enabled=True,
        mcp_impl=lambda *_a, **_k: {
            "ok": False,
            "error_code": "CITY_NOT_FOUND",
            "message": "没有查到",
        },
    )
    check("CITY_NOT_FOUND 有 error", "没有查到" in city_missing.get("error", ""), str(city_missing))
    check("CITY_NOT_FOUND 不调直连", calls1["direct"] == 0, str(calls1))
    check("done 包含 weather", done1 == ["weather"], str(done1))

    out_of_range, done2, _, calls2 = _run_node(
        mcp_enabled=True,
        mcp_impl=lambda *_a, **_k: {
            "ok": False,
            "error_code": "OUT_OF_RANGE",
            "message": "日期窗口超出范围",
        },
    )
    check("OUT_OF_RANGE 有 error", "日期窗口" in out_of_range.get("error", ""), str(out_of_range))
    check("OUT_OF_RANGE 不调直连", calls2["direct"] == 0, str(calls2))
    check("done 包含 weather", done2 == ["weather"], str(done2))


def test_empty_mcp_days_fallback() -> None:
    print("\n[6] MCP 返回空 days：允许回退时走直连")
    finding, done, _, calls = _run_node(
        mcp_enabled=True,
        mcp_impl=lambda *_a, **_k: {"ok": True, "days": []},
        fallback_direct=True,
    )
    check("总 fallback 标记", finding.get("mcp_fallback") is True, str(finding))
    check("直连被调用", calls["direct"] == 2, str(calls))
    check("done 包含 weather", done == ["weather"], str(done))


def test_mcp_breaker() -> None:
    print("\n[7] mcp:weather 熔断：连续失败后直接拒绝")
    reset_all()
    original_get_client = mcp_weather._get_client
    original_enabled = settings.MCP_ENABLED

    def down():
        raise mcp_weather.MCPUnavailableError("模拟 MCP 启动失败")

    mcp_weather._get_client = down
    settings.MCP_ENABLED = True
    try:
        for _ in range(3):
            try:
                mcp_weather.get_weather("成都", date.today().isoformat(), 1)
            except mcp_weather.MCPUnavailableError:
                pass
        breaker = get_breaker("mcp:weather")
        check("连续失败后 OPEN", breaker.snapshot()["state"] == "open", str(breaker.snapshot()))
        try:
            mcp_weather.get_weather("成都", date.today().isoformat(), 1)
        except Exception as exc:
            check(
                "OPEN 后直接拒绝",
                type(exc).__name__ == "CircuitOpenError",
                type(exc).__name__,
            )
        else:
            check("OPEN 后直接拒绝", False)
    finally:
        mcp_weather._get_client = original_get_client
        settings.MCP_ENABLED = original_enabled
        reset_all()


def test_mcp_span() -> None:
    print("\n[9] MCP 调用 span：类型、父子关系、属性")
    from app.common.tracing import (
        TraceRecorder,
        activate_trace,
        new_trace_id,
        trace_span,
    )

    class FakeClient:
        def call_tool_sync(self, _name, _arguments, _timeout):
            return {
                "ok": True,
                "days": [
                    {
                        "date": date.today().isoformat(),
                        "weather": "晴",
                        "temp_max": 20,
                        "temp_min": 12,
                        "precip_prob": 0,
                    }
                ],
            }

    original_get_client = mcp_weather._get_client
    mcp_weather._get_client = lambda: FakeClient()
    reset_all()
    trace_id = new_trace_id()
    recorder = TraceRecorder(trace_id)
    try:
        with activate_trace(trace_id, recorder):
            with trace_span(
                "node.weather", "node", node_name="weather"
            ) as node:
                payload = mcp_weather.get_weather(
                    "成都", date.today().isoformat(), 1
                )
    finally:
        mcp_weather._get_client = original_get_client
        reset_all()

    spans = {span.name: span for span in recorder.spans}
    mcp_span = spans.get("mcp.weather.get_weather")
    check("MCP span 存在", mcp_span is not None)
    check("MCP payload ok", payload.get("ok") is True)
    if mcp_span is None or node is None:
        return
    check("span_type=mcp", mcp_span.span_type == "mcp")
    check("父 span 是天气节点", mcp_span.parent_span_id == node.span_id)
    check("node_name=weather", mcp_span.node_name == "weather")
    check("attributes.mcp_tool", mcp_span.attributes.get("mcp_tool") == "get_weather")
    check("attributes.mcp_ok", mcp_span.attributes.get("mcp_ok") is True)
    check(
        "attributes.mcp_latency_ms",
        isinstance(mcp_span.attributes.get("mcp_latency_ms"), int),
    )
    check("MCP span 成功", mcp_span.status == "success")


def test_real_start_failure() -> None:
    print("\n[8] 真实子进程启动失败：返回 MCPUnavailableError")
    original_enabled = settings.MCP_ENABLED
    original_module = settings.MCP_WEATHER_MODULE
    settings.MCP_ENABLED = True
    settings.MCP_WEATHER_MODULE = "app.mcp_servers.definitely_missing"
    try:
        try:
            mcp_weather.get_weather("成都", date.today().isoformat(), 1)
        except mcp_weather.MCPUnavailableError:
            check("返回 MCPUnavailableError", True)
        else:
            check("返回 MCPUnavailableError", False)
    finally:
        settings.MCP_ENABLED = original_enabled
        settings.MCP_WEATHER_MODULE = original_module
        mcp_weather.close()
        reset_all()


def main() -> int:
    test_mcp_disabled()
    test_mcp_success()
    test_mcp_unavailable_fallback()
    test_mcp_unavailable_no_fallback()
    test_business_errors_do_not_fallback()
    test_empty_mcp_days_fallback()
    test_mcp_breaker()
    test_real_start_failure()
    test_mcp_span()

    print(f"\n结果：{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
