"""验证节点级追踪的上下文、span 树、节点/LLM 埋点和中间件。

不访问网络、不写数据库：只验证内存里的 recorder 和 request 上下文。
真实落库（agent_run_spans）需要一条真实聊天消息，手工验证命令见 docs/observability.md。

用法（在 backend 目录下执行）：
    .venv/Scripts/python.exe scripts/verify_tracing.py
"""

import logging
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.common.exceptions import BusinessException
from app.common.resilience import CircuitOpenError, reset_all
from app.common.tracing import (
    Span,
    TraceIdMiddleware,
    TraceLogFilter,
    TraceRecorder,
    activate_trace,
    current_span,
    current_trace_id,
    new_span_id,
    new_trace_id,
    trace_span,
)

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


def _span(name: str, span_type: str = "node") -> Span:
    return Span(
        trace_id=new_trace_id(),
        span_id=new_span_id(),
        parent_span_id=None,
        name=name,
        span_type=span_type,
    )


# ---------- 1. span 树与上下文 ----------


def test_span_tree() -> None:
    print("\n[1] span 父子关系与上下文清理")
    trace_id = new_trace_id()
    recorder = TraceRecorder(trace_id)

    with activate_trace(trace_id, recorder):
        check("activate_trace 设置 trace_id", current_trace_id() == trace_id)
        with trace_span("run.test", "run") as root:
            check("run span 没有父节点", root is not None and root.parent_span_id is None)
            with trace_span("node.one", "node", node_name="one") as node:
                with trace_span("llm.deepseek", "llm", node_name="one") as llm_span:
                    check("LLM span 能拿到当前 span", current_span() is llm_span)
                    llm_span.tokens = 5
                node.mark_degraded("节点产物不完整")

    spans = {span.name: span for span in recorder.spans}
    root = spans.get("run.test")
    node = spans.get("node.one")
    llm_span = spans.get("llm.deepseek")

    check("三个 span 都记录了", root is not None and node is not None and llm_span is not None)
    if root is None or node is None or llm_span is None:
        return
    check("node 的父是 root", node.parent_span_id == root.span_id)
    check("llm 的父是 node", llm_span.parent_span_id == node.span_id)
    check("llm token 记录正确", llm_span.tokens == 5)
    check("node 被标成失败/降级", node.status == "failed" and node.error_code == "NODE_DEGRADED")
    check("node attributes 带 degraded", node.attributes.get("degraded") is True)
    check("root 状态成功", root.status == "success")
    check("span 有耗时", root.duration_ms >= 0 and root.end_time is not None)
    check("退出后 trace_id 清空", current_trace_id() == "")
    check("退出后 current_span 清空", current_span() is None)


def test_no_recorder() -> None:
    print("\n[2] 没有 recorder 时是空操作")
    with trace_span("noop", "node") as span:
        check("span 为 None", span is None)
    check("不抛异常", True)


# ---------- 3. 节点包装器 ----------


def test_traced_node() -> None:
    print("\n[3] 节点包装器识别 error finding / tokens")
    from app.agents.graph import _traced_node

    trace_id = new_trace_id()
    recorder = TraceRecorder(trace_id)
    wrapped = _traced_node(
        "demo",
        lambda _state: {
            "findings": {"demo": {"error": "调用失败"}},
            "tokens": 7,
        },
    )
    with activate_trace(trace_id, recorder):
        result = wrapped({})

    check("节点返回值不变", result["tokens"] == 7)
    if not recorder.spans:
        check("节点 span 存在", False)
        return
    record = recorder.spans[0]
    check("节点 span 名称", record.name == "node.demo")
    check("节点 span 类型", record.span_type == "node")
    check("节点 span 记录 node_name", record.node_name == "demo")
    check("error finding 标 failed", record.status == "failed")
    check("error_code=NODE_DEGRADED", record.error_code == "NODE_DEGRADED")
    check("节点 token 聚合", record.tokens == 7)


# ---------- 4. LLM span ----------


class _FakeMessage:
    content = "ok"
    usage_metadata = {"input_tokens": 1, "output_tokens": 1}


class _FakeStructuredClient:
    model_name = "fake-model"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def with_structured_output(self, _schema, include_raw=False, method=None):
        return self

    def invoke(self, _messages):
        if self.fail:
            raise RuntimeError("fake llm down")
        return {"raw": _FakeMessage(), "parsed": object()}


class _Schema(BaseModel):
    pass


def test_llm_span_success() -> None:
    print("\n[4] LLM 成功调用的 span")
    from app.agents import llm

    original = llm.get_llm
    reset_all()
    llm.get_llm = lambda provider="deepseek": _FakeStructuredClient()
    trace_id = new_trace_id()
    recorder = TraceRecorder(trace_id)
    try:
        with activate_trace(trace_id, recorder):
            with trace_span("node.planner", "node", node_name="planner") as node:
                parsed, tokens = llm.invoke_structured(
                    "p", [], "", _Schema, node="planner"
                )
    finally:
        llm.get_llm = original
        reset_all()

    spans = {span.name: span for span in recorder.spans}
    span = spans.get("llm.deepseek")
    check("结构化结果非空", parsed is not None)
    check("token 透传", tokens == 2)
    check("LLM span 存在", span is not None)
    if span is None or node is None:
        return
    check("LLM span 的父是节点", span.parent_span_id == node.span_id)
    check("LLM span token", span.tokens == 2)
    check("attributes provider", span.attributes.get("provider") == "deepseek")
    check("attributes model", span.attributes.get("model") == "fake-model")
    check("attributes parsed_ok", span.attributes.get("parsed_ok") is True)
    check("LLM span 成功", span.status == "success")


def test_llm_span_failure() -> None:
    print("\n[5] LLM 抛异常时 span 标 failed")
    from app.agents import llm

    original = llm.get_llm
    reset_all()
    llm.get_llm = lambda provider="deepseek": _FakeStructuredClient(fail=True)
    trace_id = new_trace_id()
    recorder = TraceRecorder(trace_id)
    try:
        with activate_trace(trace_id, recorder):
            with trace_span("node.planner", "node", node_name="planner"):
                try:
                    llm.invoke_structured("p", [], "", _Schema, node="planner")
                except RuntimeError:
                    pass
    finally:
        llm.get_llm = original
        reset_all()

    span = next((s for s in recorder.spans if s.span_type == "llm"), None)
    check("LLM span 存在", span is not None)
    if span is None:
        return
    check("LLM span 标 failed", span.status == "failed")
    check("error_code=RuntimeError", span.error_code == "RuntimeError")
    check("不记录异常原文", span.error_message is None)


# ---------- 6. 中间件 ----------


def test_middleware() -> None:
    print("\n[6] TraceIdMiddleware：/api 生成并回传，忽略客户端伪造")
    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"trace_id": current_trace_id()}

    @app.get("/health")
    def health():
        return {"ok": True}

    app.add_middleware(TraceIdMiddleware)
    client = TestClient(app)

    first = client.get("/api/ping", headers={"X-Trace-ID": "spoofed"})
    second = client.get("/api/ping")
    trace_id = first.headers.get("x-trace-id")

    check("响应带 X-Trace-ID", bool(trace_id))
    check("trace_id 是 32 位", len(trace_id or "") == 32, str(trace_id))
    check("不采用客户端伪造值", trace_id != "spoofed")
    check("路由里拿到的 trace_id 一致", first.json()["trace_id"] == trace_id)
    check(
        "每次请求生成新 id",
        second.headers.get("x-trace-id") != trace_id,
        f"{trace_id} vs {second.headers.get('x-trace-id')}",
    )
    check("非 /api 请求不加头", "x-trace-id" not in client.get("/health").headers)


# ---------- 7. 日志 filter ----------


def test_log_filter() -> None:
    print("\n[7] 日志 filter 注入 trace/span")
    trace_id = new_trace_id()
    recorder = TraceRecorder(trace_id)
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", (), None)

    with activate_trace(trace_id, recorder):
        with trace_span("node.log", "node") as span:
            TraceLogFilter().filter(record)
            check("record.trace_id", record.trace_id == trace_id)
            check("record.span_id", record.span_id == span.span_id)

    outside = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", (), None)
    TraceLogFilter().filter(outside)
    check("没有 trace 时显示 -", outside.trace_id == "-" and outside.span_id == "-")


# ---------- 8. 错误摘要安全 ----------


def test_safe_error_message() -> None:
    print("\n[8] span 错误摘要不落敏感异常原文")
    span = _span("demo")
    span.fail(RuntimeError("postgresql://user:pass@host/db?key=secret"))
    check("未知异常只留类名", span.error_message is None)
    check("error_code 保留", span.error_code == "RuntimeError")

    business = _span("business")
    business.fail(BusinessException("会话不存在", 404))
    check("业务异常保留安全文案", business.error_message == "会话不存在")

    breaker = _span("breaker")
    breaker.fail(CircuitOpenError("llm:deepseek"))
    check(
        "熔断异常保留依赖名",
        "llm:deepseek" in (breaker.error_message or ""),
        str(breaker.error_message),
    )


# ---------- 9. recorder 上限 ----------


def test_recorder_cap() -> None:
    print("\n[9] recorder 上限")
    recorder = TraceRecorder(new_trace_id(), max_spans=2)
    for index in range(3):
        recorder.add(_span(f"span.{index}"))
    check("超过上限的 span 被丢弃", len(recorder.spans) == 2, str(len(recorder.spans)))
    recorder.flush(None)
    check("flush(None) 不报错", True)


# ---------- 10. send_message 包装器 ----------


def test_send_message_wrapper() -> None:
    print("\n[10] send_message 包装器：根 span + run id flush")
    from app.services import message_service

    instances = []

    class FakeRecorder:
        def __init__(self, trace_id: str) -> None:
            self.trace_id = trace_id
            self.spans = []
            self.flushed = None
            instances.append(self)

        def add(self, span) -> None:
            self.spans.append(span)

        def flush(self, run_id) -> None:
            self.flushed = run_id

    original = message_service.TraceRecorder
    message_service.TraceRecorder = FakeRecorder
    try:
        @message_service._traced_send_message
        def dummy():
            # 模拟第一段 commit 后设置 run id
            message_service._run_id_var.set(123)
            with trace_span("node.inner", "node") as inner:
                inner.tokens = 4
            return "ok"

        result = dummy()
    finally:
        message_service.TraceRecorder = original

    check("返回值不变", result == "ok")
    check("创建了 recorder", len(instances) == 1)
    if not instances:
        return
    recorder = instances[0]
    check("run id 被 flush", recorder.flushed == 123, str(recorder.flushed))
    by_name = {span.name: span for span in recorder.spans}
    root = by_name.get("run.send_message")
    inner = by_name.get("node.inner")
    check("根 span 存在", root is not None)
    check("内部 span 存在", inner is not None)
    if root is None or inner is None:
        return
    check("根 span 是 run 类型", root.span_type == "run")
    check("根 span 没有父", root.parent_span_id is None)
    check("内部 span 父是根", inner.parent_span_id == root.span_id)
    check("run id 上下文已清理", message_service._run_id_var.get() is None)


# ---------- 11. ORM 映射 ----------


def test_row_mapping() -> None:
    print("\n[11] span -> agent_run_spans 行映射")
    from app.common.tracing import _to_row
    from app.models.agent_run_span import AgentRunSpan

    span = _span("n" * 200, "node")
    span.node_name = "x" * 80
    span.status = "success"
    span.duration_ms = 12
    span.tokens = 3
    span.error_message = "e" * 600
    row = _to_row(AgentRunSpan, span, 42)

    check("run_id 写入", row.run_id == 42)
    check("name 截断到 100", len(row.name) == 100)
    check("node_name 截断到 50", len(row.node_name) == 50)
    check("duration 保留", row.duration_ms == 12)
    check("tokens 保留", row.tokens == 3)
    check("error_message 截断到 500", len(row.error_message) == 500)
    check("status 保留", row.status == "success")


# ---------- 12. LangGraph 上下文传播 ----------


def test_langgraph_propagation() -> None:
    print("\n[12] contextvar 能穿过 LangGraph 的同步执行")
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    from app.agents.graph import _traced_node

    class State(TypedDict):
        x: str

    def node(_state):
        return {"x": current_trace_id()}

    graph = StateGraph(State)
    graph.add_node("n", _traced_node("n", node))
    graph.add_edge(START, "n")
    graph.add_edge("n", END)
    compiled = graph.compile()

    trace_id = new_trace_id()
    recorder = TraceRecorder(trace_id)
    with activate_trace(trace_id, recorder):
        result = compiled.invoke({"x": ""})

    check("节点里拿到的 trace_id 一致", result["x"] == trace_id)
    check("节点 span 被记录", [s.name for s in recorder.spans] == ["node.n"])


# ---------- 13. 并发隔离 ----------


def test_concurrent_traces() -> None:
    print("\n[13] 两个并发上下文的 trace/span 不串")
    results: dict[str, tuple[str, str, str]] = {}
    recorders: dict[str, TraceRecorder] = {}

    def worker(trace_id: str) -> None:
        recorder = TraceRecorder(trace_id)
        recorders[trace_id] = recorder
        with activate_trace(trace_id, recorder):
            with trace_span("node.work", "node") as span:
                time.sleep(0.02)
                inside = current_trace_id()
                span_trace = span.trace_id if span is not None else ""
        after = current_trace_id()
        results[trace_id] = (inside, span_trace, after)

    first = new_trace_id()
    second = new_trace_id()
    threads = [
        threading.Thread(target=worker, args=(trace_id,))
        for trace_id in (first, second)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5.0)

    for trace_id in (first, second):
        result = results.get(trace_id)
        check(f"{trace_id[:6]} 内部 trace 正确", bool(result) and result[0] == trace_id)
        check(f"{trace_id[:6]} span trace 正确", bool(result) and result[1] == trace_id)
        check(f"{trace_id[:6]} 退出后清空", bool(result) and result[2] == "")
        check(
            f"{trace_id[:6]} 自己的 recorder 只含自己的 span",
            all(s.trace_id == trace_id for s in recorders[trace_id].spans),
        )


# ---------- 14. flush 编排 ----------


def test_flush_with_fake_session() -> None:
    print("\n[14] flush 使用独立 session，失败不影响调用方")
    import app.database as database

    class FakeSession:
        def __init__(self) -> None:
            self.rows = []
            self.committed = False
            self.rolled_back = False
            self.closed = False

        def add_all(self, rows) -> None:
            self.rows.extend(rows)

        def commit(self) -> None:
            self.committed = True

        def rollback(self) -> None:
            self.rolled_back = True

        def close(self) -> None:
            self.closed = True

    original = database.SessionLocal
    try:
        fake = FakeSession()
        database.SessionLocal = lambda: fake
        recorder = TraceRecorder(new_trace_id())
        recorder.add(_span("a", "node"))
        recorder.flush(7)

        check("flush 写入 run_id", bool(fake.rows) and fake.rows[0].run_id == 7)
        check("flush 提交", fake.committed)
        check("flush 关闭 session", fake.closed)

        class FailingSession(FakeSession):
            def add_all(self, rows) -> None:
                raise RuntimeError("db down")

        failing = FailingSession()
        database.SessionLocal = lambda: failing
        recorder = TraceRecorder(new_trace_id())
        recorder.add(_span("b", "node"))
        recorder.flush(8)
        check("落库失败不抛异常", True)
        check("落库失败回滚", failing.rolled_back)
        check("落库失败关闭 session", failing.closed)
    finally:
        database.SessionLocal = original


def main() -> int:
    test_span_tree()
    test_no_recorder()
    test_traced_node()
    test_llm_span_success()
    test_llm_span_failure()
    test_middleware()
    test_log_filter()
    test_safe_error_message()
    test_recorder_cap()
    test_send_message_wrapper()
    test_row_mapping()
    test_langgraph_propagation()
    test_concurrent_traces()
    test_flush_with_fake_session()

    print(f"\n结果：{_passed} passed, {_failed} failed")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
