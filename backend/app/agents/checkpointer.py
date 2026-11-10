"""LangGraph 的 Postgres 检查点——多轮记忆的落点。

图每次执行完会把状态存进 Postgres，下次同一个 thread_id 进来时自动读回，
于是"那酒店呢"这种省略主语的追问也能被理解。这就是"多轮对话记忆"。

本模块只管生命周期（建连接池、建表、退出时收尾），不关心图怎么用。
"""

import logging
import threading

from app.config import settings

logger = logging.getLogger(__name__)

_pool = None
_checkpointer = None
_initialized = False
# 与 cache.py / llm.py 同一套写法：服务启动时多个请求可能同时进到这里
_lock = threading.Lock()


def _to_psycopg3_url(url: str) -> str:
    """去掉连接串里的驱动后缀，psycopg3 只认 postgresql:// 格式。"""
    return url.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


def get_checkpointer():
    """返回 PostgresSaver 单例；初始化失败时返回 None，图仍可无记忆运行。"""
    # 三个变量都必须声明 global；漏掉 _pool 会让 close() 永远无法释放连接池。
    global _checkpointer, _pool, _initialized

    if _initialized:
        return _checkpointer

    with _lock:
        if not _initialized:
            try:
                _checkpointer, _pool = _build()
            except Exception as e:
                logger.warning("检查点初始化失败，本次运行不带多轮记忆：%s", e)
                _checkpointer, _pool = None, None
            _initialized = True
    return _checkpointer


def _build():
    """建连接池 + 建表，返回 (saver, pool)。"""
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg_pool import ConnectionPool

    url = settings.CHECKPOINT_DB_URL or _to_psycopg3_url(settings.DATABASE_URL)

    # 服务需要长期存活的连接池，不用仅适合脚本的 from_conn_string()。
    pool = ConnectionPool(
        conninfo=url,
        min_size=1,
        max_size=4,
        # 不阻塞启动，建连在后台进行。
        open=False,
        # autocommit 让建表语句独立生效；prepare_threshold=0 避免复用连接时
        # 出现 prepared statement already exists。
        kwargs={"autocommit": True, "prepare_threshold": 0},
    )
    pool.open(wait=False)

    saver = PostgresSaver(pool)
    # setup() 幂等创建检查点三张表。
    saver.setup()
    logger.info("LangGraph 检查点已就绪，表建在 %s", url.split("@")[-1])
    return saver, pool


def close() -> None:
    """关闭连接池，由应用 lifespan 退出时调用，避免闲置连接耗尽数据库。"""
    global _pool, _checkpointer, _initialized
    if _pool is not None:
        try:
            _pool.close()
        except Exception as e:
            logger.warning("关闭检查点连接池失败：%s", e)
    _pool = None
    _checkpointer = None
    _initialized = False


def thread_id_for(conversation_id: int) -> str:
    """把业务会话 id 映射为 LangGraph thread_id，前缀用于区分检查点来源。"""
    return f"conv-{conversation_id}"


def delete_thread_for(conversation_id: int) -> None:
    """删除会话在 LangGraph 检查点表中的全部记录。

    检查点与 conversations 无外键，删除会话不会级联清理，必须显式调用。
    参数是业务会话 id，内部完成 thread_id 转换；失败只记 warning，
    不能因为清理失败让删除会话接口返回 500。
    服务重启后也要按需初始化 checkpointer，才能清掉上次运行留下的数据。
    """
    checkpointer = get_checkpointer()
    if checkpointer is None:
        # 本次运行没有可用的检查点存储，也就不会有需要清理的多轮记忆
        return
    try:
        checkpointer.delete_thread(thread_id_for(conversation_id))
    except Exception as e:
        logger.warning("清理会话 %s 的检查点失败（不影响会话删除）：%s", conversation_id, e)
