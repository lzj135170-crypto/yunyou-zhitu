"""Redis 旁路缓存封装。

缓存不可用或数据损坏一律视为未命中，不抛异常、不影响主流程；出错只记 warning。
Redis 不可用时退到进程内 TTL 缓存；多 worker 部署请确保 Redis 可用，
否则各进程的降级缓存互不共享，失效通知也到不了另一个进程。
"""

import hashlib
import json
import logging
import threading
import time
from typing import Any

import redis

from app.config import settings

logger = logging.getLogger(__name__)

# 进程内复用客户端；redis-py 自带连接池。
_client: redis.Redis | None = None

# 保护客户端初始化，避免并发创建多个连接池。
_client_lock = threading.Lock()

# Redis 不可用时退到进程内 TTL 缓存。只作为降级层：
# Redis 正常时不会读它，避免多 worker 下读到别的进程的旧值。
_local: dict[str, tuple[float, str]] = {}
_local_lock = threading.Lock()
_LOCAL_MAX_ITEMS = 512
_REDIS_RETRY_AFTER_SECONDS = 300
_redis_retry_at = 0.0


def _redis_ready() -> bool:
    return time.monotonic() >= _redis_retry_at


def _mark_redis_down() -> None:
    global _redis_retry_at
    _redis_retry_at = time.monotonic() + _REDIS_RETRY_AFTER_SECONDS


def _mark_redis_up() -> None:
    """Redis 恢复后丢掉降级副本，避免旧值盖住 Redis 里的新值。"""
    global _redis_retry_at
    if _redis_retry_at:
        _redis_retry_at = 0.0
        _local_clear()


def _local_get(key: str) -> str | None:
    now = time.monotonic()
    with _local_lock:
        item = _local.get(key)
        if item is None:
            return None
        expires_at, raw = item
        if expires_at <= now:
            _local.pop(key, None)
            return None
        return raw


def _local_set(key: str, raw: str, ttl_seconds: int) -> None:
    if ttl_seconds <= 0:
        return
    now = time.monotonic()
    with _local_lock:
        if len(_local) >= _LOCAL_MAX_ITEMS:
            expired = [k for k, (expires_at, _) in _local.items() if expires_at <= now]
            for k in expired:
                _local.pop(k, None)
            while len(_local) >= _LOCAL_MAX_ITEMS:
                _local.pop(next(iter(_local)), None)
        _local[key] = (now + ttl_seconds, raw)


def _local_delete(*keys: str) -> None:
    with _local_lock:
        for key in keys:
            _local.pop(key, None)


def _local_clear() -> None:
    with _local_lock:
        _local.clear()


def probe() -> bool:
    """启动时探一次 Redis；失败就把降级状态提前设好，别让首个请求等超时。"""
    try:
        get_client().ping()
        _mark_redis_up()
        return True
    except Exception as e:
        _mark_redis_down()
        logger.warning("Redis 不可用，结果缓存降级为进程内缓存：%s", e)
        return False


def get_client() -> redis.Redis:
    """返回 Redis 客户端，首次调用时创建。

    from_url 不会立即建立连接，Redis 未启动时服务仍可启动并降级。
    """
    global _client

    if _client is not None:
        return _client

    with _client_lock:
        if _client is None:
            _client = redis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                # 快速失败，避免缓存超时拖慢请求。
                socket_connect_timeout=2,
                socket_timeout=2,
                # 定期探活，避免连接池积压死连接。
                health_check_interval=30,
            )
    return _client


# 业务 key 集中构造，避免 service 之间循环依赖。

# 缓存载荷结构版本；结构变化时递增，旧 key 自动失效。
_SCHEMA_VERSION = 2

_HISTORY_PREFIX = f"travel:history:v{_SCHEMA_VERSION}:"
# 结果投影增加了跨运行补齐与提示清洗，旧缓存里可能还是缺板块的旧结构；
# 用独立版本号失效，不牵连消息历史缓存。
_RESULT_PREFIX = "travel:result:v4:"
_HOTELS_PREFIX = f"travel:hotels:v{_SCHEMA_VERSION}:"


def history_key(conversation_id: int) -> str:
    """某个会话的消息历史缓存 key，形如 travel:history:v2:42。"""
    return f"{_HISTORY_PREFIX}{conversation_id}"


def plan_key(conversation_id: int) -> str:
    """结果页行程投影的缓存 key，新回复成功后会主动删除。"""
    return f"{_RESULT_PREFIX}plan:{conversation_id}"


def hotels_key(destination: str, specs: list[tuple[str, str]]) -> str:
    """按「目的地 + 住宿区域/档位」缓存高德酒店检索结果。

    key 不绑定会话：同一城市、同一组区域可以复用，减少高德 QPS。
    行程换了区域/档位，key 自然变化，不需要逐条失效。
    """
    parts = [destination.strip()]
    parts.extend(f"{area.strip()}\t{level.strip()}" for area, level in specs)
    digest = hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()
    return f"{_HOTELS_PREFIX}{digest}"


# ==========================================================================
# 读写封装：全部吞掉异常
# ==========================================================================


def get_json(key: str) -> Any | None:
    """读一个 JSON 值。未命中、出错、内容损坏，一律返回 None（调用方都当作未命中）。"""
    # 先读进程内副本：Redis 故障时这是唯一的快速路径；
    # Redis 正常时本地通常为空（写入成功会清掉本地副本）。
    raw = _local_get(key)

    if raw is None and _redis_ready():
        try:
            raw = get_client().get(key)
            _mark_redis_up()
        except Exception as e:
            # Redis 故障不是业务故障，标记冷却后继续用本地副本。
            _mark_redis_down()
            logger.warning("Redis 读取失败 key=%s：%s", key, e)

    if raw is None:
        return None

    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        # 脏数据按未命中处理，并删除，避免反复解析失败。
        logger.warning("缓存中 key=%s 的内容不是合法 JSON，已删除", key)
        delete(key)
        return None


def set_json(key: str, value: Any, ttl_seconds: int) -> None:
    """写一个 JSON 值并设置过期时间。Redis 不可用时写入进程内降级缓存。"""
    # 中文原样存储，便于 redis-cli 排查。
    raw = json.dumps(value, ensure_ascii=False)

    if _redis_ready():
        try:
            get_client().set(key, raw, ex=ttl_seconds)
            _mark_redis_up()
            _local_delete(key)
            return
        except Exception as e:
            _mark_redis_down()
            logger.warning("Redis 写入失败 key=%s：%s", key, e)

    _local_set(key, raw, ttl_seconds)


def delete(*keys: str) -> None:
    """删除若干 key；失败只记日志，旧数据由 TTL 兜底。"""
    if not keys:
        return

    _local_delete(*keys)

    if not _redis_ready():
        return

    try:
        get_client().delete(*keys)
        _mark_redis_up()
    except Exception as e:
        _mark_redis_down()
        logger.warning("Redis 删除失败 keys=%s：%s", keys, e)
