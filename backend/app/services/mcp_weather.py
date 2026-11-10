"""Weather / POI MCP 客户端的同步桥接层。

MCP SDK 是异步的，这里用后台线程运行独立 asyncio loop，持久化 ClientSession，
把同步调用转成队列请求。请求必须在同一个 task 中进入和退出 session，
因此由 `_main` 持有上下文并消费队列，关闭时投递 None。

天气和 POI 共用一个子进程，但使用不同的熔断器和 span：
天气 `mcp:weather`，POI `mcp:amap`。transport 失败抛 MCPUnavailableError；
业务错误是正常 payload。失败后 60 秒内不重试。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import sys
import threading
import time
from datetime import timedelta

from app.common.resilience import (
    BreakerOutcome,
    CircuitOpenError,
    get_breaker,
)
from app.common.tracing import current_span, trace_span
from app.config import BASE_DIR, settings

logger = logging.getLogger(__name__)

try:  # 依赖没装时不能让天气节点 import 失败、把整个应用带崩。
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    _MCP_AVAILABLE = True
except ImportError:  # pragma: no cover - 只在没装 mcp 的环境触发
    ClientSession = None  # type: ignore[assignment]
    StdioServerParameters = None  # type: ignore[assignment]
    stdio_client = None  # type: ignore[assignment]
    _MCP_AVAILABLE = False


class MCPUnavailableError(RuntimeError):
    """MCP server 没起来、进程挂了、超时或协议错误。"""


def _classify_mcp_exception(exc: BaseException) -> BreakerOutcome:
    if isinstance(exc, CircuitOpenError):
        return BreakerOutcome.IGNORE
    return BreakerOutcome.FAILURE


def _classify_mcp_result(payload: object) -> BreakerOutcome:
    # 能拿到合法 payload 就说明 MCP server 是活的。
    # payload 里的 ok=false 是上游业务错误（天气或 POI），不该把 MCP 熔断器打开。
    return (
        BreakerOutcome.SUCCESS
        if isinstance(payload, dict)
        else BreakerOutcome.FAILURE
    )


def _stdio_params():
    env = os.environ.copy()
    # 子进程 stdout 写 MCP JSON-RPC，必须是 UTF-8；中文城市名不能靠 cp936 碰运气。
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", settings.MCP_WEATHER_MODULE],
        cwd=str(BASE_DIR),
        env=env,
        encoding="utf-8",
    )


class _MCPWeatherClient:
    """一个持久化的 MCP 客户端：后台 loop + 单一 session。"""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._thread_main, name="mcp-weather", daemon=True
        )
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._queue: asyncio.Queue | None = None
        self._closed = False

    def start(self) -> None:
        if not _MCP_AVAILABLE:
            raise MCPUnavailableError("MCP SDK 未安装")
        self._thread.start()
        timeout = max(5.0, settings.MCP_WEATHER_TOOL_TIMEOUT_SECONDS)
        if not self._ready.wait(timeout):
            raise MCPUnavailableError("MCP weather server 启动超时")
        if self._start_error is not None:
            raise MCPUnavailableError(
                f"MCP weather server 启动失败：{type(self._start_error).__name__}"
            )
        if self._queue is None:
            raise MCPUnavailableError("MCP weather session 未建立")

    def _thread_main(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except BaseException as exc:  # noqa: BLE001 - 启动失败要传给主线程
            self._start_error = exc
            self._ready.set()
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _main(self) -> None:
        try:
            if settings.MCP_WEATHER_TRANSPORT == "streamable-http":
                from mcp.client.streamable_http import streamablehttp_client

                timeout = max(5.0, settings.MCP_WEATHER_TOOL_TIMEOUT_SECONDS)
                async with streamablehttp_client(
                    settings.MCP_WEATHER_URL, timeout=timeout
                ) as (read, write, _):
                    await self._run_session(read, write)
            else:
                async with stdio_client(_stdio_params()) as (read, write):
                    await self._run_session(read, write)
        except BaseException as exc:  # noqa: BLE001 - 启动失败要传给主线程
            self._start_error = exc
            self._ready.set()

    async def _run_session(self, read, write) -> None:
        timeout = timedelta(seconds=settings.MCP_WEATHER_TOOL_TIMEOUT_SECONDS)
        async with ClientSession(
            read, write, read_timeout_seconds=timeout
        ) as session:
            await session.initialize()
            self._queue = asyncio.Queue()
            # 队列建好之后才通知主线程：避免 start() 返回后 call_tool_sync
            # 看到一个还没准备好的队列。
            self._ready.set()
            await self._serve(session)

    async def _serve(self, session) -> None:
        queue = self._queue
        assert queue is not None
        while True:
            item = await queue.get()
            if item is None:
                break
            future, name, arguments, timeout = item
            if future.cancelled():
                continue
            try:
                result = await session.call_tool(
                    name,
                    arguments=arguments,
                    read_timeout_seconds=timedelta(seconds=timeout),
                )
            except Exception as exc:  # noqa: BLE001 - 转成客户端可见的错误
                if not future.cancelled():
                    future.set_exception(
                        MCPUnavailableError(
                            f"MCP tool {name} 调用失败：{type(exc).__name__}"
                        )
                    )
            else:
                if not future.cancelled():
                    future.set_result(result)

    def call_tool_sync(
        self,
        name: str,
        arguments: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        queue = self._queue
        if queue is None:
            raise MCPUnavailableError("MCP weather session 未建立")

        timeout = timeout or settings.MCP_WEATHER_TOOL_TIMEOUT_SECONDS
        future: concurrent.futures.Future = concurrent.futures.Future()
        try:
            self._loop.call_soon_threadsafe(
                queue.put_nowait,
                (future, name, arguments or {}, float(timeout)),
            )
        except RuntimeError as exc:
            raise MCPUnavailableError("MCP weather loop 已停止") from exc

        try:
            result = future.result(timeout=float(timeout) + 1.0)
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise MCPUnavailableError("MCP weather 调用超时") from exc

        if getattr(result, "isError", False):
            raise MCPUnavailableError(f"MCP tool {name} 返回 isError")

        payload = getattr(result, "structuredContent", None)
        if payload is None:
            texts = [
                block.text
                for block in (getattr(result, "content", None) or [])
                if getattr(block, "type", "") == "text"
            ]
            if not texts:
                raise MCPUnavailableError(f"MCP tool {name} 没有返回内容")
            try:
                payload = json.loads("".join(texts))
            except (TypeError, ValueError) as exc:
                raise MCPUnavailableError(
                    f"MCP tool {name} 返回了非 JSON 内容"
                ) from exc

        # FastMCP 对 dict 返回值有时会包一层 {"result": ...}；这里兼容一下。
        if (
            isinstance(payload, dict)
            and set(payload) == {"result"}
            and isinstance(payload["result"], dict)
        ):
            payload = payload["result"]

        if not isinstance(payload, dict):
            raise MCPUnavailableError(f"MCP tool {name} 返回了非对象 payload")
        return payload

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        queue = self._queue
        if queue is not None:
            try:
                self._loop.call_soon_threadsafe(queue.put_nowait, None)
            except RuntimeError:
                pass
        if self._thread.is_alive():
            self._thread.join(timeout=5.0)
        if self._thread.is_alive():
            # 兜底：请求没有及时退出时终止 loop；子进程通常由 stdio_client
            # 的清理/Job Object 收尾，这里至少不把后台线程留着。
            try:
                self._loop.call_soon_threadsafe(self._loop.stop)
            except RuntimeError:
                pass


_client: _MCPWeatherClient | None = None
_client_lock = threading.Lock()
_next_retry_at = 0.0


def _get_client() -> _MCPWeatherClient:
    global _client, _next_retry_at

    if not settings.MCP_ENABLED:
        raise MCPUnavailableError("MCP 未启用")
    if not _MCP_AVAILABLE:
        raise MCPUnavailableError("MCP SDK 未安装")

    with _client_lock:
        if _client is not None:
            return _client

        now = time.monotonic()
        if now < _next_retry_at:
            raise MCPUnavailableError("MCP weather server 暂不可用")

        client = _MCPWeatherClient()
        try:
            client.start()
        except Exception as exc:
            _next_retry_at = (
                time.monotonic() + settings.MCP_WEATHER_DOWN_RETRY_SECONDS
            )
            try:
                client.close()
            except Exception:
                pass
            # 只记类型：启动异常里可能带完整命令/环境变量。
            logger.warning(
                "MCP weather server 启动失败：%s", type(exc).__name__
            )
            raise MCPUnavailableError(
                "MCP weather server 启动失败"
            ) from exc

        _client = client
        return client


def _drop_client(client: _MCPWeatherClient) -> None:
    global _client, _next_retry_at

    with _client_lock:
        if _client is client:
            _client = None
            _next_retry_at = (
                time.monotonic() + settings.MCP_WEATHER_DOWN_RETRY_SECONDS
            )
    try:
        client.close()
    except Exception:
        pass


def call_tool_sync(
    name: str,
    arguments: dict | None = None,
    timeout: float | None = None,
    *,
    breaker: str = "mcp:weather",
    span_prefix: str = "mcp.weather",
) -> dict:
    """调用 MCP tool，返回结构化 payload。

    span 在调用方线程开启，contextvars 不跨线程传播。
    breaker/span_prefix 用于区分天气和 POI，两工具组共用子进程但故障独立可查。
    """
    started = time.perf_counter()
    parent = current_span()
    with trace_span(
        f"{span_prefix}.{name}",
        "mcp",
        node_name=(parent.node_name if parent is not None else None),
        attributes={"mcp_tool": name},
    ) as span:
        def _invoke() -> dict:
            client = _get_client()
            try:
                return client.call_tool_sync(name, arguments, timeout)
            except Exception:
                # transport/协议错误后 session 可能已经不可用；丢掉，
                # 让下一次调用在冷却后重新拉起。
                _drop_client(client)
                raise

        try:
            payload = get_breaker(breaker).call(
                _invoke,
                classify_exception=_classify_mcp_exception,
                classify_result=_classify_mcp_result,
            )
        except CircuitOpenError:
            if span is not None:
                span.attributes["circuit_open"] = True
            raise
        finally:
            if span is not None:
                span.attributes["mcp_latency_ms"] = int(
                    (time.perf_counter() - started) * 1000
                )

        if span is not None:
            span.attributes["mcp_ok"] = bool(
                isinstance(payload, dict) and payload.get("ok")
            )
        return payload


def get_weather(
    city: str,
    start_date: str,
    days: int,
    timeout: float | None = None,
) -> dict:
    """天气节点使用的组合 tool：一次返回 geocode + forecast。"""
    return call_tool_sync(
        "get_weather",
        {"city": city, "start_date": start_date, "days": days},
        timeout,
    )


def search_pois(
    city: str,
    keywords: str,
    types: str = "",
    limit: int = 8,
    timeout: float | None = None,
) -> dict:
    """景点/美食/住宿专家使用的 POI tool。

    返回 ok/items 或 ok:false；业务错误不抛异常，transport 超时才抛 MCPUnavailableError。
    """
    return call_tool_sync(
        "search_poi",
        {"city": city, "keywords": keywords, "types": types, "limit": limit},
        timeout,
        breaker="mcp:amap",
        span_prefix="mcp.amap",
    )


def close() -> None:
    """关闭客户端和 MCP 子进程。应用 lifespan 退出时调用。"""
    global _client, _next_retry_at

    with _client_lock:
        client = _client
        _client = None
        _next_retry_at = 0.0
    if client is not None:
        try:
            client.close()
        except Exception:
            pass
