"""消息业务逻辑，以及"发消息 → 拿回复"的编排。

send_message 是服务层唯一的多行写事务：用户消息、AI 回复和运行记录同成同败。
读函数不 commit。list_messages 使用旁路缓存，数据库始终是唯一事实来源。
"""

import logging
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common import cache
from app.common.exceptions import BusinessException
from app.common.tracing import (
    TraceRecorder,
    activate_trace,
    current_trace_id,
    new_trace_id,
    trace_span,
)
from app.config import settings
from app.models.agent_run import AgentRun
from app.models.message import Message
from app.services import trip_persist_service
from app.services.agent import generate_reply
from app.services.conversation_service import get_owned_conversation

logger = logging.getLogger(__name__)

# system 消息不能返回给前端，避免泄露提示词。
HIDDEN_ROLES = ("system",)

# 自动标题截取长度，适配侧边栏。
AUTO_TITLE_MAX_LEN = 30

# 每个会话缓存最新 100 条；更长会话的翻页会穿透到数据库，但属于低频操作。
HISTORY_CACHE_WINDOW = 100


def _serialize_messages(rows: list[Message]) -> list[dict]:
    """把消息转成可 JSON 序列化的 dict；不能直接序列化 ORM 内部状态。"""
    return [
        {
            "id": m.id,
            "conversation_id": m.conversation_id,
            "role": m.role,
            "content": m.content,
            "agent_name": m.agent_name,
            "create_time": m.create_time.isoformat() if m.create_time else None,
            "update_time": m.update_time.isoformat() if m.update_time else None,
        }
        for m in rows
    ]


def _deserialize_message(data: dict) -> Message:
    """把缓存 dict 还原为游离 Message；只能读标量字段，访问关系属性会抛异常。"""
    return Message(
        id=data["id"],
        conversation_id=data["conversation_id"],
        role=data["role"],
        content=data["content"],
        agent_name=data["agent_name"],
        create_time=datetime.fromisoformat(data["create_time"]) if data["create_time"] else None,
        update_time=datetime.fromisoformat(data["update_time"]) if data["update_time"] else None,
    )


def _slice_from_window(
    messages: list[dict], complete: bool, limit: int, before_id: int | None
) -> tuple[list[dict], bool] | None:
    """从缓存窗口切出请求页；窗口覆盖不到时返回 None，由调用方回退数据库。"""
    ids = [m["id"] for m in messages]

    if before_id is None:
        # 要最新一页：从窗口尾部切
        end = len(messages)
    else:
        if before_id not in ids:
            # 游标不在窗口里（用户翻得太深），缓存帮不上忙
            return None
        end = ids.index(before_id)

    start = max(0, end - limit)

    # 窗口不完整且已切到窗口开头时，缓存无法回答，必须回退数据库；
    # 否则翻页会少给消息且难以复现。
    if start == 0 and not complete:
        return None

    page = messages[start:end]

    # 还有更早的消息 = 窗口内这一页之前仍有内容，或者窗口本身就没覆盖到会话开头
    has_more = start > 0 or not complete
    return page, has_more


def _fetch_window(db: Session, conversation_id: int) -> tuple[list[Message], bool]:
    """从数据库取出最新缓存窗口，返回 (按 id 升序消息, 是否覆盖会话开头)。"""
    rows = (
        db.query(Message)
        .filter(
            Message.conversation_id == conversation_id,
            Message.role.notin_(HIDDEN_ROLES),
        )
        .order_by(Message.id.desc())
        # 多查一条用来判断"窗口是否已经覆盖到会话开头"：
        # 查出来正好是 WINDOW+1 条，说明后面还有更早的，complete 为假。
        .limit(HISTORY_CACHE_WINDOW + 1)
        .all()
    )
    complete = len(rows) <= HISTORY_CACHE_WINDOW
    rows = rows[:HISTORY_CACHE_WINDOW]
    rows.reverse()  # 上面倒序取是为了拿"最新的一批"，写进缓存前翻回正序
    return rows, complete


def _write_cache(conversation_id: int, rows: list[Message], complete: bool) -> None:
    """把窗口内容写进 Redis，过期时间取配置里的 CACHE_TTL_SECONDS。"""
    cache.set_json(
        cache.history_key(conversation_id),
        {"messages": _serialize_messages(rows), "complete": complete},
        settings.CACHE_TTL_SECONDS,
    )


def _is_valid_payload(cached: Any) -> bool:
    """校验缓存载荷结构；异常结构按未命中处理，避免返回错误的分页结果。"""
    return (
        isinstance(cached, dict)
        and isinstance(cached.get("messages"), list)
        and isinstance(cached.get("complete"), bool)
    )


# ==========================================================================
# 对外接口
# ==========================================================================


def list_messages(
    db: Session,
    conversation_id: int,
    user_id: int,
    limit: int = 50,
    before_id: int | None = None,
) -> tuple[list[Message], bool]:
    """分页查询会话消息，返回 (按 id 升序的一页, 是否还有更早消息)。

    必须先校验会话归属再读缓存，否则会绕过权限。
    游标使用 id 而非 create_time，避免同秒并列导致漏消息或重复。
    """
    get_owned_conversation(db, conversation_id, user_id)

    key = cache.history_key(conversation_id)
    cached = cache.get_json(key)

    if cached is not None and not _is_valid_payload(cached):
        # 有值但不是我们写的那个结构。删掉这个脏 key 并当作未命中——
        # 不删的话每次读都要白解析一遍，而且永远命中不了。
        # 记一条日志：这种情况静默处理掉的话，将来很难查清缓存为什么一直不命中。
        logger.warning("缓存 key=%s 的结构不符合预期，已删除并回退数据库", key)
        cache.delete(key)
        cached = None

    if cached is not None:
        # 结构已经校验过，这里可以放心直接取字段，不用 .get() 兜底
        sliced = _slice_from_window(
            cached["messages"], cached["complete"], limit, before_id
        )
        if sliced is not None:
            items, has_more = sliced
            return [_deserialize_message(d) for d in items], has_more
        # 走到这里说明请求的那一页超出了缓存窗口，继续往下走数据库

    if before_id is None:
        # 要的是最新一页（也就是用户点击会话时的那次请求）。
        # 这里特意按"缓存窗口"的大小来查，而不是只查 limit+1 条，
        # 一次查询同时满足两件事：返回请求的这一页 + 顺便把缓存写热。
        # 分成两次查的话，每个会话首次被点开时都要多打一次数据库。
        rows, complete = _fetch_window(db, conversation_id)
        _write_cache(conversation_id, rows, complete)

        # 注意返回的是请求的那一页（最多 limit 条），不是整个窗口
        has_more = len(rows) > limit or not complete
        return (rows[-limit:] if limit < len(rows) else rows), has_more

    # 翻更早的页、且超出了缓存窗口：直接查库，不刷新缓存。
    # 用户一路翻到很久以前属于低频操作，为它重刷整个窗口不划算。
    rows = (
        db.query(Message)
        .filter(
            Message.conversation_id == conversation_id,
            Message.role.notin_(HIDDEN_ROLES),
            Message.id < before_id,
        )
        .order_by(Message.id.desc())
        # 多取一条用来判断"还有没有更早的"，省掉一次全表 COUNT
        .limit(limit + 1)
        .all()
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    rows.reverse()
    return rows, has_more


# 第一段 commit 之后才有的 run id。装饰器在 finally 里读它，
# 用来把本次请求缓冲的 span 落库。
_run_id_var: ContextVar[int | None] = ContextVar("message_run_id", default=None)


def _traced_send_message(fn):
    """给 send_message 加运行级 trace。

    根 span 从函数入口就开始，覆盖两次 commit、整张图、行程投影和缓存预热；
    span 先放内存，run id 在第一段 commit 后才有，所以最后统一 flush。
    flush 失败只会在 TraceRecorder 里记 warning，不会影响已经提交的消息。
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        # 64 是 agent_runs.trace_id 的列宽；正常路径都是 32 位 uuid4().hex，
        # 这里截断只是防御外部上下文传入了更长的自定义 id。
        trace_id = (current_trace_id() or new_trace_id())[:64]
        recorder = TraceRecorder(trace_id)
        run_token = _run_id_var.set(None)
        try:
            with activate_trace(trace_id, recorder):
                with trace_span("run.send_message", "run"):
                    return fn(*args, **kwargs)
        finally:
            run_id = _run_id_var.get()
            _run_id_var.reset(run_token)
            if run_id is not None:
                recorder.flush(run_id)

    return wrapper


@_traced_send_message
def send_message(
    db: Session, conversation_id: int, user_id: int, content: str
) -> tuple[Message, Message]:
    """发送消息并生成 AI 回复，返回已提交的 (用户消息, AI 回复)。

    两段式提交：第一段提交用户消息和 running 运行记录后归还数据库连接，
    模型调用期间不占连接；第二段写回复并回填运行记录。
    模型失败时用户消息保留，运行记录标 failed，这是断链的唯一线索。
    进程在调用期间被杀会留下 running 僵尸记录，属于两段式的固有代价。
    """
    # 1. 校验归属，并持有会话对象用于更新标题和活跃时间。
    conversation = get_owned_conversation(db, conversation_id, user_id)

    # 2. 落用户消息；autoflush 关闭，需手动 flush 获取自增 id。
    user_message = Message(conversation_id=conversation.id, role="user", content=content)
    db.add(user_message)
    db.flush()

    now = datetime.now(timezone.utc)

    # 3. 首条消息自动标题，并刷新会话活跃时间和计数。
    # 必须在第一次 commit 前完成：commit 会让 ORM 对象过期，之后访问会多一次 SELECT。
    if not conversation.title:
        conversation.title = " ".join(content.split())[:AUTO_TITLE_MAX_LEN]

    # 显式赋值，不能依赖 onupdate：没有其他列变化时 SQLAlchemy 不会发 UPDATE。
    conversation.update_time = now

    # 反规范化计数，避免列表页逐会话 COUNT。
    conversation.message_count = (conversation.message_count or 0) + 1
    conversation.last_message_at = user_message.create_time or now

    # 4. 建 running 运行记录，和用户消息一起提交。
    agent_run = AgentRun(
        conversation_id=conversation_id,
        user_id=user_id,
        request_message_id=user_message.id,
        agent_name="travel_agent",
        input={"message_id": user_message.id, "content": content},
        status="running",
        # 脚本调用时没有中间件，兜底生成 trace_id。
        trace_id=current_trace_id() or uuid.uuid4().hex,
    )
    db.add(agent_run)
    try:
        with trace_span("db.commit.initial", "db"):
            db.commit()
        # 第一段提交后才有 run_id，供装饰器落 span。
        _run_id_var.set(agent_run.id)
    except IntegrityError:
        # 会话在提交前被删除，外键约束失败；统一映射为会话不存在。
        db.rollback()
        raise BusinessException(message="会话不存在", code=404) from None

    # 5. 调智能体；提交后连接已归还，模型调用期间不占连接。
    started_at = time.perf_counter()
    try:
        reply = generate_reply(db, conversation, user_message)
    except Exception as exc:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
        # 标记失败再抛；回填失败不能掩盖原始异常。
        try:
            agent_run.status = "failed"
            agent_run.latency = latency_ms
            agent_run.finished_at = datetime.now(timezone.utc)
            agent_run.error_code = type(exc).__name__[:50]
            agent_run.error_message = "智能体执行失败，请稍后重试"
            db.commit()
        except Exception:
            logger.warning("回填失败状态时出错（会话 %s）", conversation_id, exc_info=True)
            db.rollback()
        raise

    latency_ms = int((time.perf_counter() - started_at) * 1000)

    # 6. 写 AI 回复并回填运行结果（第二段）。
    assistant_message = Message(
        conversation_id=conversation_id,
        role="assistant",
        content=reply.content,
        agent_name=reply.agent_name,
    )
    db.add(assistant_message)
    # 先 flush 拿到 assistant_message.id，才能写进 run.response_message_id。
    db.flush()

    conversation.message_count = (conversation.message_count or 0) + 1
    conversation.last_message_at = assistant_message.create_time or datetime.now(timezone.utc)

    agent_run.agent_name = reply.agent_name
    agent_run.output = reply.output
    agent_run.status = "success"
    agent_run.total_tokens = reply.total_tokens
    agent_run.latency = latency_ms
    agent_run.finished_at = datetime.now(timezone.utc)
    agent_run.response_message_id = assistant_message.id
    run_id = agent_run.id

    try:
        with trace_span("db.commit.final", "db"):
            db.commit()
    except IntegrityError:
        # 会话在两次提交之间被删除，或 agent_run 被级联删除。
        db.rollback()
        raise BusinessException(message="会话不存在", code=404) from None

    # 7. 投影行程到规范化表；独立事务，失败不回滚已提交的回复。
    try:
        with trace_span("db.trip_persist", "db"):
            trip_persist_service.sync_run_trip(db, run_id, force=True)
    except Exception:
        logger.warning("行程规范化落库失败（run %s）", run_id, exc_info=True)

    # sync_run_trip 的 commit 会让对象过期；refresh 保证返回最新字段。
    db.refresh(user_message)
    db.refresh(assistant_message)

    # 8. 重建历史缓存：直接删再按数据库预热，避免增量更新导致缓存不一致。
    # 放在 commit 之后；Redis 失败只影响性能，TTL 会兜底。
    with trace_span("cache.history_refresh", "cache"):
        # 新回复可能改变结果页投影，缓存必须一起失效。
        cache.delete(
            cache.history_key(conversation_id),
            cache.plan_key(conversation_id),
        )
        rows, complete = _fetch_window(db, conversation_id)
        _write_cache(conversation_id, rows, complete)

    return user_message, assistant_message
