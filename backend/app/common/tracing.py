"""轻量节点级追踪：一次消息运行内部的 span 树。

同一轮请求共享 trace_id，span 落库到 agent_run_spans 供事后查询。
没有 recorder 时埋点为空操作，落库失败只记 warning，不影响用户回复。
同步执行依赖 FastAPI 线程池复制 contextvars；独立线程需显式复制上下文。
字段按 OTel 的 span_id/parent_span_id 语义设计，便于以后接入。
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Iterator

from starlette.datastructures import MutableHeaders

from app.common.exceptions import BusinessException
from app.common.resilience import CircuitOpenError

logger = logging.getLogger(__name__)

# 一次运行最多保留多少 span。正常一轮十几到几十个；
# 500 足够覆盖重试和以后的 RAG/DB 细拆，同时防止某个异常路径无限追加把内存吃满。
MAX_SPANS_PER_RUN = 500

# 日志格式。%(trace_id)s / %(span_id)s 由 TraceLogFilter 注入；
# 没有 trace 的日志（比如进程启动阶段）显示 "-"。
LOG_FORMAT = (
    "%(asctime)s %(levelname)s "
    "[trace=%(trace_id)s span=%(span_id)s] %(name)s: %(message)s"
)

# 当前请求的 trace_id。由 TraceIdMiddleware 或 message_service 设置。
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")

# 当前 span。span 的嵌套关系靠它建立父子关系。
current_span_var: ContextVar["Span | None"] = ContextVar(
    "current_span", default=None
)

# 当前运行的 recorder。没有它时 trace_span 是空操作。
recorder_var: ContextVar["TraceRecorder | None"] = ContextVar(
    "trace_recorder", default=None
)


def new_trace_id() -> str:
    """32 位十六进制，和 agent_runs.trace_id 的长度一致。"""
    return uuid.uuid4().hex


def new_span_id() -> str:
    """16 位十六进制，按 OpenTelemetry 的 span_id 语义设计。"""
    return uuid.uuid4().hex[:16]


def current_trace_id() -> str:
    """当前请求的 trace_id；没有时返回空串。"""
    trace_id = trace_id_var.get()
    if trace_id:
        return trace_id
    recorder = recorder_var.get()
    return recorder.trace_id if recorder is not None else ""


def current_span() -> "Span | None":
    return current_span_var.get()


def current_span_id() -> str:
    span = current_span()
    return span.span_id if span is not None else ""


def current_recorder() -> "TraceRecorder | None":
    return recorder_var.get()


@dataclass
class Span:
    """span 的内存表示，与 agent_run_spans 列对应；耗时用 monotonic 计算，不受系统时间跳变影响。"""

    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    span_type: str
    node_name: str | None = None
    status: str = "running"
    start_time: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    end_time: datetime | None = None
    duration_ms: int = 0
    tokens: int = 0
    error_code: str | None = None
    error_message: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    _started_monotonic: float = field(
        default_factory=time.monotonic, repr=False, compare=False
    )

    def finish(self) -> None:
        """结束 span；还没被显式标状态的一律算成功。"""
        if self.end_time is None:
            self.end_time = datetime.now(timezone.utc)
        elapsed = (time.monotonic() - self._started_monotonic) * 1000
        self.duration_ms = max(0, int(round(elapsed)))
        if self.status == "running":
            self.status = "success"

    def fail(self, exc: BaseException) -> None:
        """标记失败；只对已知安全的异常保留原文，其他异常只记录类名，避免泄露 URL/连接串。"""
        self.status = "failed"
        self.error_code = type(exc).__name__[:50]
        self.error_message = None
        self.attributes["error_type"] = type(exc).__name__
        if isinstance(exc, (BusinessException, CircuitOpenError)):
            self.error_message = str(exc)[:500]

    def mark_degraded(
        self, message: str = "节点返回降级结果", *, failed: bool = True
    ) -> None:
        """标记节点已运行但产物不完整；failed=True 时同时标为 failed，便于聚合。"""
        self.attributes["degraded"] = True
        if failed:
            self.status = "failed"
            self.error_code = "NODE_DEGRADED"
            self.error_message = message[:500]


class TraceRecorder:
    """一次运行的 span 缓冲。

    图执行期间不能落库：run_id 尚未产生，且在图中写事务会破坏两段式提交边界。
    先缓存在内存，send_message 结束后用独立 Session 批量 flush。
    """

    def __init__(self, trace_id: str, max_spans: int = MAX_SPANS_PER_RUN):
        self.trace_id = trace_id
        self.max_spans = max_spans
        self._lock = Lock()
        self._spans: list[Span] = []
        self._flushed = False
        self._dropped = 0

    @property
    def spans(self) -> list[Span]:
        with self._lock:
            return list(self._spans)

    def add(self, span: Span) -> None:
        with self._lock:
            if self._flushed:
                return
            if len(self._spans) >= self.max_spans:
                self._dropped += 1
                if self._dropped == 1:
                    logger.warning(
                        "trace %s 的 span 数超过上限 %s，后续 span 丢弃",
                        self.trace_id,
                        self.max_spans,
                    )
                return
            self._spans.append(span)

    def flush(self, run_id: int | None) -> None:
        """批量写入 agent_run_spans。

        使用独立 Session：不复用调用方事务，失败也不回滚已提交的消息。
        """
        if run_id is None:
            return

        with self._lock:
            if self._flushed:
                return
            self._flushed = True
            spans = list(self._spans)
            dropped = self._dropped

        if not spans:
            if dropped:
                logger.warning(
                    "trace %s 丢弃了 %s 个超出上限的 span",
                    self.trace_id,
                    dropped,
                )
            return

        db = None
        try:
            from app.database import SessionLocal
            from app.models.agent_run_span import AgentRunSpan

            db = SessionLocal()
            db.add_all([_to_row(AgentRunSpan, span, run_id) for span in spans])
            db.commit()
            if dropped:
                logger.warning(
                    "trace %s 丢弃了 %s 个超出上限的 span",
                    self.trace_id,
                    dropped,
                )
        except Exception as exc:
            if db is not None:
                try:
                    db.rollback()
                except Exception:
                    pass
            # 只记类名：连接失败/约束冲突的原文里可能带连接串或整行数据。
            logger.warning(
                "trace %s 的 span 落库失败（run=%s）：%s",
                self.trace_id,
                run_id,
                type(exc).__name__,
            )
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass


def _to_row(model, span: Span, run_id: int):
    """Span -> ORM 行。所有字符串列在这里统一截断，避免超长写入失败。"""
    return model(
        run_id=run_id,
        trace_id=span.trace_id[:64],
        span_id=span.span_id[:32],
        parent_span_id=(
            span.parent_span_id[:32] if span.parent_span_id else None
        ),
        name=span.name[:100],
        span_type=span.span_type[:20],
        node_name=(span.node_name[:50] if span.node_name else None),
        status=span.status,
        start_time=span.start_time,
        end_time=span.end_time,
        duration_ms=max(0, int(span.duration_ms)),
        tokens=max(0, int(span.tokens)),
        error_code=(span.error_code[:50] if span.error_code else None),
        error_message=(
            span.error_message[:500] if span.error_message else None
        ),
        attributes=span.attributes or None,
    )


@contextmanager
def activate_trace(
    trace_id: str, recorder: TraceRecorder
) -> Iterator[TraceRecorder]:
    """绑定 trace_id/recorder 到当前上下文，退出时用 token 恢复外层值。"""
    trace_token: Token = trace_id_var.set(trace_id)
    recorder_token: Token = recorder_var.set(recorder)
    try:
        yield recorder
    finally:
        recorder_var.reset(recorder_token)
        trace_id_var.reset(trace_token)


@contextmanager
def trace_span(
    name: str,
    span_type: str = "node",
    *,
    node_name: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[Span | None]:
    """创建 span；无 recorder 时为空操作。

    异常先标记再原样抛出；节点自行吞掉的异常不会经过这里，需单独标记 degraded。
    """
    recorder = current_recorder()
    if recorder is None:
        yield None
        return

    # run 类型是新一次运行的根，必须没有父 span。
    # 否则如果 send_message 碰巧被包在别的 span 里，根 span 会指向一个
    # 不在本次 recorder 里的 parent，查询时看起来像"父节点丢了"。
    parent = None if span_type == "run" else current_span()
    span = Span(
        trace_id=recorder.trace_id,
        span_id=new_span_id(),
        parent_span_id=parent.span_id if parent is not None else None,
        name=name,
        span_type=span_type,
        node_name=node_name,
        attributes=dict(attributes or {}),
    )
    token: Token = current_span_var.set(span)
    try:
        yield span
    except BaseException as exc:
        span.fail(exc)
        raise
    finally:
        span.finish()
        recorder.add(span)
        current_span_var.reset(token)


class TraceIdMiddleware:
    """为每个 /api 请求生成 trace_id 并通过响应头返回。

    不信任客户端传入的 X-Trace-ID，避免伪造链路或撞唯一约束。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not (path == "/api" or path.startswith("/api/")):
            await self.app(scope, receive, send)
            return

        trace_id = new_trace_id()
        token = trace_id_var.set(trace_id)

        async def send_with_trace(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Trace-ID"] = trace_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace)
        finally:
            trace_id_var.reset(token)


class TraceLogFilter(logging.Filter):
    """把 trace/span 注入日志 record；挂在 handler 上以覆盖子 logger 传播记录。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = current_trace_id() or "-"
        record.span_id = current_span_id() or "-"
        return True


def configure_logging(level: int = logging.INFO) -> None:
    """给应用日志加 trace/span 格式。

    仅当 root 无 handler 时新建；已有 handler 只补 filter，
    不替换 formatter，避免打乱 uvicorn 的颜色日志。
    """
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
        )
        root.addHandler(handler)

    for handler in root.handlers:
        if not any(isinstance(item, TraceLogFilter) for item in handler.filters):
            handler.addFilter(TraceLogFilter())

    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)
