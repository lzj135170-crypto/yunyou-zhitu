"""验证熔断器：状态机、依赖隔离、10021 限流分类，以及现有降级是否还在。

用法（在 backend 目录下执行）：
    .venv/Scripts/python.exe scripts/verify_resilience.py

这个脚本不访问网络、不写数据库、不重启容器。高德和 LLM 都用假函数替换，
只验证熔断器本身的判断，以及接入点在被熔断/失败时是否按预期降级。
"""

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.common import resilience
from app.common.resilience import (
    BreakerOutcome,
    BreakerPolicy,
    CircuitBreaker,
    CircuitOpenError,
    get_breaker,
    reset_all,
    snapshot_all,
)
from app.config import settings

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


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _boom() -> None:
    raise RuntimeError("boom")


def _fail_classifier(_exc: BaseException) -> BreakerOutcome:
    return BreakerOutcome.FAILURE


# ---------- 1. 状态机 ----------


def test_state_machine() -> None:
    print("\n[1] 状态机 CLOSED -> OPEN -> HALF_OPEN -> CLOSED/OPEN")
    clock = FakeClock()
    breaker = CircuitBreaker(
        "test:state", BreakerPolicy(3, 10.0, 1), clock=clock
    )

    check("初始 CLOSED", breaker.snapshot()["state"] == "closed")
    check("正常调用成功", breaker.call(lambda: "ok") == "ok")

    for _ in range(3):
        try:
            breaker.call(_boom, classify_exception=_fail_classifier)
        except RuntimeError:
            pass
    snap = breaker.snapshot()
    check("连续 3 次失败后 OPEN", snap["state"] == "open")
    check("open_total == 1", snap["open_total"] == 1, str(snap))
    check("failure_count == 3", snap["failure_count"] == 3, str(snap))

    ran = {"value": False}

    def should_not_run():
        ran["value"] = True

    try:
        breaker.call(should_not_run)
    except CircuitOpenError:
        check("OPEN 时直接拒绝", True)
    else:
        check("OPEN 时直接拒绝", False)
    check("被拒绝的调用没有执行", ran["value"] is False)
    check(
        "rejected_total == 1",
        breaker.snapshot()["rejected_total"] == 1,
        str(breaker.snapshot()),
    )

    clock.advance(10.0)
    check("冷却后放行探测", breaker.call(lambda: "probe") == "probe")
    check("探测成功 -> CLOSED", breaker.snapshot()["state"] == "closed")
    check("关闭后 failure_count 清零", breaker.snapshot()["failure_count"] == 0)

    for _ in range(3):
        try:
            breaker.call(_boom, classify_exception=_fail_classifier)
        except RuntimeError:
            pass
    check("再次 OPEN", breaker.snapshot()["state"] == "open")
    check("open_total == 2", breaker.snapshot()["open_total"] == 2)

    clock.advance(10.0)
    try:
        breaker.call(_boom, classify_exception=_fail_classifier)
    except RuntimeError:
        pass
    check("半开探测失败 -> 重新 OPEN", breaker.snapshot()["state"] == "open")
    check("open_total == 3", breaker.snapshot()["open_total"] == 3)


# ---------- 2. 半开只放一个探测 ----------


def test_half_open_single_probe() -> None:
    print("\n[2] HALF_OPEN 只放一个探测")
    clock = FakeClock()
    breaker = CircuitBreaker(
        "test:half-open", BreakerPolicy(1, 5.0, 1), clock=clock
    )
    try:
        breaker.call(_boom, classify_exception=_fail_classifier)
    except RuntimeError:
        pass
    clock.advance(5.0)

    started = threading.Event()
    release = threading.Event()
    result: dict[str, object] = {}

    def slow_success():
        started.set()
        release.wait(2.0)
        return "probe-ok"

    def probe() -> None:
        try:
            result["value"] = breaker.call(slow_success)
        except Exception as e:  # noqa: BLE001 - 测试里只记录结果
            result["error"] = e

    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    started.wait(2.0)

    rejected = False
    try:
        breaker.call(lambda: "second-probe")
    except CircuitOpenError:
        rejected = True
    release.set()
    thread.join(timeout=5.0)

    check("探测进行中，第二个请求被拒绝", rejected)
    check("探测成功", result.get("value") == "probe-ok", str(result))
    check("探测成功后 CLOSED", breaker.snapshot()["state"] == "closed")


# ---------- 3. 结果分类：10021 不计失败 ----------


def test_throttle_does_not_open() -> None:
    print("\n[3] THROTTLE 不计失败")
    clock = FakeClock()
    breaker = CircuitBreaker(
        "test:throttle", BreakerPolicy(3, 10.0, 1), clock=clock
    )
    for _ in range(10):
        breaker.call(
            lambda: {"status": "0", "infocode": "10021"},
            classify_result=lambda _: BreakerOutcome.THROTTLE,
        )
    snap = breaker.snapshot()
    check("10 次限流后仍 CLOSED", snap["state"] == "closed", str(snap))
    check("failure_count == 0", snap["failure_count"] == 0, str(snap))
    check("throttle_total == 10", snap["throttle_total"] == 10, str(snap))


# ---------- 4. 依赖隔离 ----------


def test_isolation() -> None:
    print("\n[4] 依赖隔离")
    clock = FakeClock()
    a = CircuitBreaker("test:a", BreakerPolicy(1, 10.0, 1), clock=clock)
    b = CircuitBreaker("test:b", BreakerPolicy(1, 10.0, 1), clock=clock)
    try:
        a.call(_boom, classify_exception=_fail_classifier)
    except RuntimeError:
        pass
    check("a 已 OPEN", a.snapshot()["state"] == "open")
    check("b 仍 CLOSED", b.snapshot()["state"] == "closed")
    check("b 仍可调用", b.call(lambda: "ok") == "ok")


# ---------- 5. 并发 ----------


def test_concurrency() -> None:
    print("\n[5] 并发下状态不乱")
    clock = FakeClock()
    breaker = CircuitBreaker(
        "test:concurrent", BreakerPolicy(3, 10.0, 1), clock=clock
    )
    workers = 20
    barrier = threading.Barrier(workers)

    def worker() -> None:
        barrier.wait()
        try:
            breaker.call(_boom, classify_exception=_fail_classifier)
        except (RuntimeError, CircuitOpenError):
            pass

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    snap = breaker.snapshot()
    check("并发后状态为 OPEN", snap["state"] == "open", str(snap))
    check("open_total 只加一次", snap["open_total"] == 1, str(snap))

    try:
        breaker.call(lambda: "after-open")
    except CircuitOpenError:
        check("OPEN 后新请求被拒绝", True)
    else:
        check("OPEN 后新请求被拒绝", False)


# ---------- 6. 高德接入 ----------


def test_amap_integration() -> None:
    print("\n[6] 高德接入：amap:json / amap:static")
    from app.services import amap_map

    original_get_json = amap_map._get_json
    original_static_once = amap_map._fetch_static_map_once
    original_delay = amap_map.QPS_RETRY_DELAY_SECONDS

    try:
        # 6.1 连续网络失败 -> OPEN，之后不再打上游
        reset_all()
        calls = {"n": 0}

        def failing_get_json(*_args, **_kwargs):
            calls["n"] += 1
            raise amap_map.AmapUpstreamError("模拟高德网络失败")

        amap_map._get_json = failing_get_json
        for _ in range(5):
            try:
                amap_map._get_json_with_qps_retry(None, "u", {})
            except amap_map.AmapUpstreamError:
                pass

        snap = get_breaker("amap:json").snapshot()
        check("高德连续失败 5 次后 OPEN", snap["state"] == "open", str(snap))
        before = calls["n"]
        try:
            amap_map._get_json_with_qps_retry(None, "u", {})
        except amap_map.AmapUpstreamError as e:
            check("熔断时返回现有异常类型", "熔断" in str(e), str(e))
        check("熔断时不再打上游", calls["n"] == before, f"{calls['n']} vs {before}")

        # 6.2 10021 只计限流，不打开熔断；内部仍重试一次
        reset_all()
        amap_map.QPS_RETRY_DELAY_SECONDS = 0.0
        calls = {"n": 0}

        def throttled_get_json(*_args, **_kwargs):
            calls["n"] += 1
            return {"status": "0", "infocode": "10021", "info": "limited"}

        amap_map._get_json = throttled_get_json
        for _ in range(10):
            payload = amap_map._get_json_with_qps_retry(None, "u", {})
            if payload.get("infocode") != "10021":
                check("限流返回原 payload", False, str(payload))
                break
        snap = get_breaker("amap:json").snapshot()
        check("10021 十次后仍 CLOSED", snap["state"] == "closed", str(snap))
        check("failure_count == 0", snap["failure_count"] == 0, str(snap))
        check("throttle_total == 10", snap["throttle_total"] == 10, str(snap))
        check("每次调用仍重试一次", calls["n"] == 20, f"实际 {calls['n']}")

        # 6.3 静态图连续失败 -> OPEN，之后不执行内部函数
        reset_all()
        static_calls = {"n": 0}

        def failing_static(*_args, **_kwargs):
            static_calls["n"] += 1
            raise amap_map.AmapUpstreamError("模拟静态图失败")

        amap_map._fetch_static_map_once = failing_static
        for _ in range(5):
            try:
                amap_map._fetch_static_map(None, "k", [])
            except amap_map.AmapUpstreamError:
                pass
        snap = get_breaker("amap:static").snapshot()
        check("静态图连续失败 5 次后 OPEN", snap["state"] == "open", str(snap))
        before = static_calls["n"]
        try:
            amap_map._fetch_static_map(None, "k", [])
        except amap_map.AmapUpstreamError as e:
            check("静态图熔断后直接失败", "熔断" in str(e), str(e))
        check(
            "静态图熔断后不再执行内部函数",
            static_calls["n"] == before,
            f"{static_calls['n']} vs {before}",
        )

        # 6.4 4xx 是 key/参数问题，不计失败
        reset_all()

        def forbidden_get_json(*_args, **_kwargs):
            raise amap_map.AmapUpstreamError(
                "模拟 key 无效", http_status=403
            )

        amap_map._get_json = forbidden_get_json
        for _ in range(10):
            try:
                amap_map._get_json_with_qps_retry(None, "u", {})
            except amap_map.AmapUpstreamError:
                pass
        snap = get_breaker("amap:json").snapshot()
        check("4xx 十次后仍 CLOSED", snap["state"] == "closed", str(snap))
        check("4xx 不计失败", snap["failure_count"] == 0, str(snap))
    finally:
        amap_map._get_json = original_get_json
        amap_map._fetch_static_map_once = original_static_once
        amap_map.QPS_RETRY_DELAY_SECONDS = original_delay
        reset_all()


# ---------- 6b. 高德瓦片接入 ----------


def test_tile_integration() -> None:
    print("\n[6b] 高德接入：amap:tile 兜底透明图")
    from app.api import amap as amap_api

    original_fetch = amap_api._fetch_tile
    try:
        reset_all()
        calls = {"n": 0}

        def failing_fetch(*_args, **_kwargs):
            calls["n"] += 1
            raise amap_api._TileUpstreamError("模拟瓦片失败")

        amap_api._fetch_tile = failing_fetch
        for _ in range(20):
            response = amap_api._proxy_tile(1, 2, 3)
            if response.headers.get("x-tile-fallback") != "1":
                check("瓦片失败时返回透明图", False, str(response.headers))
                break
        else:
            check("瓦片失败时返回透明图", True)

        snap = get_breaker("amap:tile").snapshot()
        check("瓦片连续失败 20 次后 OPEN", snap["state"] == "open", str(snap))
        before = calls["n"]
        response = amap_api._proxy_tile(1, 2, 3)
        check(
            "熔断后仍返回透明图",
            response.headers.get("x-tile-fallback") == "1",
            str(response.headers),
        )
        check("熔断后不再打上游", calls["n"] == before, f"{calls['n']} vs {before}")
    finally:
        amap_api._fetch_tile = original_fetch
        reset_all()


# ---------- 7. LLM 接入 ----------


class _FakeMessage:
    content = "ok"
    usage_metadata = {"input_tokens": 1, "output_tokens": 1}


class _FakeStructured:
    def __init__(self, provider: str, fail: bool) -> None:
        self.provider = provider
        self.fail = fail

    def invoke(self, _messages):
        if self.fail:
            raise RuntimeError(f"{self.provider} down")
        return {"raw": _FakeMessage(), "parsed": object()}


class _FakeLLM:
    def __init__(self, provider: str, fail: bool) -> None:
        self.provider = provider
        self.fail = fail

    def with_structured_output(self, _schema, include_raw=False, method=None):
        return _FakeStructured(self.provider, self.fail)

    def invoke(self, _messages):
        if self.fail:
            raise RuntimeError(f"{self.provider} down")
        return _FakeMessage()


def test_llm_integration() -> None:
    print("\n[7] LLM 接入：熔断、MiMo 回退、文本调用")
    from app.agents import llm
    from app.agents.nodes.planner import PlanDecision

    original_get_llm = llm.get_llm
    try:
        # 7.1 DeepSeek 连续失败 -> OPEN -> 返回 (None, 0)
        reset_all()
        llm.get_llm = lambda provider="deepseek": _FakeLLM(provider, fail=True)
        for _ in range(3):
            try:
                llm.invoke_structured(
                    "p", [], "", PlanDecision, node="planner"
                )
            except RuntimeError:
                pass
        snap = get_breaker("llm:deepseek").snapshot()
        check("DeepSeek 3 次失败后 OPEN", snap["state"] == "open", str(snap))
        parsed, tokens = llm.invoke_structured(
            "p", [], "", PlanDecision, node="planner"
        )
        check("DeepSeek OPEN 返回 (None, 0)", parsed is None and tokens == 0)

        # 7.2 MiMo 熔断 -> 回退 DeepSeek
        reset_all()
        clients = {
            "mimo": _FakeLLM("mimo", fail=True),
            "deepseek": _FakeLLM("deepseek", fail=False),
        }
        llm.get_llm = lambda provider="deepseek": clients[provider]
        for _ in range(3):
            try:
                llm.invoke_structured(
                    "p", [], "", PlanDecision, node="attraction"
                )
            except RuntimeError:
                pass
        check(
            "MiMo 3 次失败后 OPEN",
            get_breaker("llm:mimo").snapshot()["state"] == "open",
            str(get_breaker("llm:mimo").snapshot()),
        )
        parsed, tokens = llm.invoke_structured(
            "p", [], "", PlanDecision, node="attraction"
        )
        check("MiMo OPEN 回退 DeepSeek 成功", parsed is not None)
        check("回退调用返回 token", tokens == 2, f"tokens={tokens}")
        check(
            "DeepSeek 没有被误开",
            get_breaker("llm:deepseek").snapshot()["state"] == "closed",
            str(get_breaker("llm:deepseek").snapshot()),
        )

        # 7.3 文本调用同样熔断 -> (None, 0)
        reset_all()
        llm.get_llm = lambda provider="deepseek": _FakeLLM(provider, fail=True)
        for _ in range(3):
            try:
                llm.invoke_text("p", [], "", node="response")
            except RuntimeError:
                pass
        text, tokens = llm.invoke_text("p", [], "", node="response")
        check("文本调用 OPEN 返回 (None, 0)", text is None and tokens == 0)
    finally:
        llm.get_llm = original_get_llm
        reset_all()


# ---------- 8. Planner 首次调用异常不再 500 ----------


def test_planner_fallback() -> None:
    print("\n[8] Planner 首次 LLM 异常被兜底")
    from app.agents.nodes import planner

    original = planner.invoke_structured
    try:
        def boom(*_args, **_kwargs):
            raise RuntimeError("planner down")

        planner.invoke_structured = boom
        tasks, tokens = planner._plan_with_llm(
            {"messages": [], "user_query": "成都三日游"}
        )
        check("异常不再上抛", tasks == [] and tokens == 0, f"{tasks}, {tokens}")
    finally:
        planner.invoke_structured = original


# ---------- 9. 注册表 ----------


def test_snapshot_all() -> None:
    print("\n[9] 状态快照")
    reset_all()
    snap = snapshot_all()
    check("包含全部依赖", set(snap["dependencies"]) == set(resilience.POLICIES))
    check("初始全部 CLOSED", all(
        item["state"] == "closed" for item in snap["dependencies"].values()
    ))
    get_breaker("amap:json").record_failure("TestError")
    snap = snapshot_all()
    check(
        "record_failure 会计数",
        snap["dependencies"]["amap:json"]["failure_count"] == 1,
        str(snap["dependencies"]["amap:json"]),
    )
    reset_all()


def main() -> int:
    original_enabled = settings.CIRCUIT_BREAKER_ENABLED
    settings.CIRCUIT_BREAKER_ENABLED = True
    try:
        test_state_machine()
        test_half_open_single_probe()
        test_throttle_does_not_open()
        test_isolation()
        test_concurrency()
        test_amap_integration()
        test_tile_integration()
        test_llm_integration()
        test_planner_fallback()
        test_snapshot_all()
    finally:
        settings.CIRCUIT_BREAKER_ENABLED = original_enabled
        reset_all()

    print(f"\n结果：{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
