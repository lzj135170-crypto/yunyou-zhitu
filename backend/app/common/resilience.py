"""按依赖隔离的轻量熔断器。

覆盖高德、LLM 和 MCP；RAG/Redis/checkpointer 已有降级路径，不重复包装。
状态保存在进程内：避免每次判断都访问 Redis，也避免 Redis 故障时熔断器失效。
多 worker 各自维护状态，最坏是多打几次上游。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Any, Callable, TypeVar

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T")


class BreakerState(str, Enum):
    """熔断器的三种状态。"""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class BreakerOutcome(str, Enum):
    """调用结果分类；THROTTLE 单独一类，高德 10021 不能算失败，否则高峰会误开熔断。"""

    SUCCESS = "success"
    FAILURE = "failure"
    THROTTLE = "throttle"
    IGNORE = "ignore"


class CircuitOpenError(RuntimeError):
    """熔断器处于 OPEN，本次调用被直接拒绝，没有真正打到上游。"""

    def __init__(self, dependency: str, retry_after_seconds: float = 0.0) -> None:
        self.dependency = dependency
        self.retry_after_seconds = retry_after_seconds
        if retry_after_seconds > 0:
            text = f"{dependency} 熔断中，约 {retry_after_seconds:.1f}s 后重试"
        else:
            text = f"{dependency} 熔断中，暂不可用"
        super().__init__(text)


@dataclass(frozen=True)
class BreakerPolicy:
    """一个依赖的熔断策略。"""

    failure_threshold: int = 5
    cooldown_seconds: float = 30.0
    half_open_max_calls: int = 1


# 未知依赖的默认策略；已知依赖使用下面 POLICIES 的显式阈值。
DEFAULT_POLICY = BreakerPolicy(
    failure_threshold=settings.CIRCUIT_BREAKER_FAILURE_THRESHOLD,
    cooldown_seconds=settings.CIRCUIT_BREAKER_COOLDOWN_SECONDS,
    half_open_max_calls=settings.CIRCUIT_BREAKER_HALF_OPEN_MAX_CALLS,
)

# 逐依赖策略；瓦片请求量大且偶发超时常见，阈值放宽到 20。
POLICIES: dict[str, BreakerPolicy] = {
    "amap:json": BreakerPolicy(
        failure_threshold=5, cooldown_seconds=30.0, half_open_max_calls=1
    ),
    "amap:static": BreakerPolicy(
        failure_threshold=5, cooldown_seconds=30.0, half_open_max_calls=1
    ),
    "amap:tile": BreakerPolicy(
        failure_threshold=20, cooldown_seconds=30.0, half_open_max_calls=1
    ),
    # LLM 单次超时 90s，连续 3 次失败基本意味着本轮对话不可用。
    "llm:deepseek": BreakerPolicy(
        failure_threshold=3, cooldown_seconds=60.0, half_open_max_calls=1
    ),
    "llm:mimo": BreakerPolicy(
        failure_threshold=3, cooldown_seconds=60.0, half_open_max_calls=1
    ),
    "mcp:weather": BreakerPolicy(
        failure_threshold=3, cooldown_seconds=60.0, half_open_max_calls=1
    ),
    # POI 和天气共进程但独立熔断：一边故障不应拖垮另一边。
    "mcp:amap": BreakerPolicy(
        failure_threshold=3, cooldown_seconds=60.0, half_open_max_calls=1
    ),
}


def _classify(
    classifier: Callable[[Any], BreakerOutcome] | None,
    value: Any,
    default: BreakerOutcome,
) -> BreakerOutcome:
    """执行分类器；分类器自身异常时按默认值处理，不能破坏熔断状态。"""
    if classifier is None:
        return default
    try:
        outcome = classifier(value)
    except Exception:
        logger.exception("熔断分类器执行失败，按 %s 处理", default.value)
        return default
    return outcome if isinstance(outcome, BreakerOutcome) else default


class CircuitBreaker:
    """单个依赖的熔断器，线程安全。

    锁只保护状态和计数，外部调用在锁外执行，避免慢调用阻塞所有线程。
    clock 可注入，测试用假时钟推进冷却。
    """

    def __init__(
        self,
        name: str,
        policy: BreakerPolicy,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.policy = policy
        self._clock = clock
        self._lock = Lock()

        self._state = BreakerState.CLOSED
        self._failure_count = 0
        self._opened_at: float | None = None
        self._opened_at_iso: str | None = None
        self._half_open_in_flight = 0

        self._success_total = 0
        self._failure_total = 0
        self._throttle_total = 0
        self._rejected_total = 0
        self._open_total = 0
        self._half_open_total = 0
        self._last_error_type = ""

    # ---------- 对外接口 ----------

    def call(
        self,
        fn: Callable[..., T],
        *args: Any,
        classify_exception: Callable[[BaseException], BreakerOutcome] | None = None,
        classify_result: Callable[[Any], BreakerOutcome] | None = None,
        **kwargs: Any,
    ) -> T:
        """执行外部调用并更新熔断状态。

        OPEN 且未冷却时抛 CircuitOpenError；冷却后进入 HALF_OPEN 放行探测。
        默认异常为失败、正常返回为成功，可由分类器覆盖。
        """
        if not settings.CIRCUIT_BREAKER_ENABLED:
            return fn(*args, **kwargs)

        allowed, retry_after, probe = self._acquire()
        if not allowed:
            raise CircuitOpenError(self.name, retry_after)

        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            outcome = _classify(
                classify_exception, exc, BreakerOutcome.FAILURE
            )
            self._release(probe, outcome, type(exc).__name__)
            raise
        except BaseException:
            # KeyboardInterrupt / SystemExit 不参与熔断判断，但必须把
            # 半开探测名额还回去，否则这个 breaker 会一直卡在 HALF_OPEN。
            self._release(probe, BreakerOutcome.IGNORE)
            raise

        outcome = _classify(classify_result, result, BreakerOutcome.SUCCESS)
        self._release(probe, outcome)
        return result

    def record_success(self) -> None:
        """外部已判定成功的调用可以手工记账（给不走 call 的接入点用）。"""
        self._release(False, BreakerOutcome.SUCCESS)

    def record_failure(self, error_type: str = "unknown") -> None:
        self._release(False, BreakerOutcome.FAILURE, error_type)

    def record_throttle(self) -> None:
        self._release(False, BreakerOutcome.THROTTLE)

    def snapshot(self) -> dict[str, Any]:
        """返回可 JSON 序列化的状态快照，供只读接口和排查脚本使用。"""
        with self._lock:
            now = self._clock()
            remaining = 0.0
            if self._state is BreakerState.OPEN and self._opened_at is not None:
                remaining = max(
                    0.0,
                    self.policy.cooldown_seconds - (now - self._opened_at),
                )
            return {
                "name": self.name,
                "state": self._state.value,
                "failure_threshold": self.policy.failure_threshold,
                "cooldown_seconds": self.policy.cooldown_seconds,
                "half_open_max_calls": self.policy.half_open_max_calls,
                "failure_count": self._failure_count,
                "success_total": self._success_total,
                "failure_total": self._failure_total,
                "throttle_total": self._throttle_total,
                "rejected_total": self._rejected_total,
                "open_total": self._open_total,
                "half_open_total": self._half_open_total,
                "last_error_type": self._last_error_type,
                "opened_at": self._opened_at_iso,
                "cooldown_remaining_seconds": round(remaining, 2),
            }

    def reset(self) -> None:
        """恢复到初始 CLOSED 状态。给测试用，生产代码不该调。"""
        with self._lock:
            self._state = BreakerState.CLOSED
            self._failure_count = 0
            self._opened_at = None
            self._opened_at_iso = None
            self._half_open_in_flight = 0
            self._success_total = 0
            self._failure_total = 0
            self._throttle_total = 0
            self._rejected_total = 0
            self._open_total = 0
            self._half_open_total = 0
            self._last_error_type = ""

    # ---------- 状态机内部 ----------

    def _acquire(self) -> tuple[bool, float, bool]:
        """判断本次调用能否放行，返回 (放行, 还需等待秒数, 是否半开探测)。"""
        now = self._clock()
        with self._lock:
            if self._state is BreakerState.CLOSED:
                return True, 0.0, False

            if self._state is BreakerState.OPEN:
                opened_at = self._opened_at if self._opened_at is not None else now
                elapsed = now - opened_at
                remaining = self.policy.cooldown_seconds - elapsed
                if remaining > 0:
                    self._rejected_total += 1
                    return False, remaining, False

                self._state = BreakerState.HALF_OPEN
                self._half_open_in_flight = 0
                self._half_open_total += 1
                logger.warning(
                    "circuit_state_change dependency=%s from=open to=half_open "
                    "cooldown=%.1fs",
                    self.name,
                    self.policy.cooldown_seconds,
                )

            # HALF_OPEN：只放固定数量的探测请求。
            if self._half_open_in_flight < self.policy.half_open_max_calls:
                self._half_open_in_flight += 1
                return True, 0.0, True

            self._rejected_total += 1
            return False, 0.0, False

    def _release(
        self,
        probe: bool,
        outcome: BreakerOutcome,
        error_type: str = "",
    ) -> None:
        """记录一次调用结果，必要时做状态转换。"""
        with self._lock:
            if probe:
                self._half_open_in_flight = max(0, self._half_open_in_flight - 1)

            if outcome is BreakerOutcome.SUCCESS:
                self._success_total += 1
                if self._state is BreakerState.HALF_OPEN:
                    self._close_locked("half_open")
                elif self._state is BreakerState.CLOSED:
                    # 连续失败计数只在 CLOSED 下有意义；一次成功就清零。
                    self._failure_count = 0

            elif outcome is BreakerOutcome.THROTTLE:
                self._throttle_total += 1
                if self._state is BreakerState.HALF_OPEN:
                    # 能收到上游的业务响应，说明链路是通的。限流是容量问题，
                    # 不是"依赖不可用"，不应该继续拦着后续请求。
                    self._close_locked("half_open")

            elif outcome is BreakerOutcome.FAILURE:
                self._failure_total += 1
                if error_type:
                    self._last_error_type = error_type
                if self._state is BreakerState.HALF_OPEN:
                    self._open_locked("half_open")
                elif self._state is BreakerState.CLOSED:
                    self._failure_count += 1
                    if self._failure_count >= self.policy.failure_threshold:
                        self._open_locked("closed")

            # IGNORE：业务错误、配置缺失等，不改变状态。

    def _open_locked(self, from_state: str) -> None:
        if self._state is BreakerState.OPEN:
            return
        self._state = BreakerState.OPEN
        self._opened_at = self._clock()
        self._opened_at_iso = datetime.now(timezone.utc).isoformat()
        self._half_open_in_flight = 0
        self._failure_count = max(
            self._failure_count, self.policy.failure_threshold
        )
        self._open_total += 1
        logger.warning(
            "circuit_state_change dependency=%s from=%s to=open "
            "failures=%s threshold=%s cooldown=%.1fs last_error=%s",
            self.name,
            from_state,
            self._failure_count,
            self.policy.failure_threshold,
            self.policy.cooldown_seconds,
            self._last_error_type or "unknown",
        )

    def _close_locked(self, from_state: str) -> None:
        self._state = BreakerState.CLOSED
        self._failure_count = 0
        self._opened_at = None
        self._opened_at_iso = None
        self._half_open_in_flight = 0
        logger.info(
            "circuit_state_change dependency=%s from=%s to=closed",
            self.name,
            from_state,
        )


_registry: dict[str, CircuitBreaker] = {}
_registry_lock = Lock()


def get_breaker(name: str) -> CircuitBreaker:
    """按依赖名取熔断器；第一次访问时创建。"""
    with _registry_lock:
        breaker = _registry.get(name)
        if breaker is None:
            breaker = CircuitBreaker(name, POLICIES.get(name, DEFAULT_POLICY))
            _registry[name] = breaker
        return breaker


def snapshot_all() -> dict[str, Any]:
    """所有已知依赖的状态快照。"""
    return {
        "enabled": bool(settings.CIRCUIT_BREAKER_ENABLED),
        "dependencies": {
            name: get_breaker(name).snapshot() for name in sorted(POLICIES)
        },
    }


def reset_all() -> None:
    """清空注册表。只给测试脚本用，生产代码不要调。"""
    with _registry_lock:
        _registry.clear()
