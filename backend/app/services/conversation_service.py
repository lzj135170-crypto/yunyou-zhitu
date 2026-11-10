"""会话业务逻辑。

全项目唯一允许按 id 解析会话的地方是 get_owned_conversation，
其他模块必须经过它，确保归属校验不被绕过。
写函数自行 commit，读函数不 commit；路由层只负责 HTTP 语义。
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.agents.checkpointer import delete_thread_for
from app.common import cache
from app.common.exceptions import BusinessException
from app.models.conversation import Conversation


def get_owned_conversation(db: Session, conversation_id: int, user_id: int) -> Conversation:
    """按 id 取会话并校验归属；不存在和无权访问统一返回 404，避免枚举。

    用一条同时过滤 id/user_id/deleted_at 的 SQL，不先用 db.get，
    防止 identity map 绕过数据库层校验，也避免漏比较 user_id。
    """
    conversation = (
        db.query(Conversation)
        .filter(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            # 软删除的会话对外等同于不存在：详情、消息、结果页、发消息都走这个入口。
            Conversation.deleted_at.is_(None),
        )
        .first()
    )
    if conversation is None:
        raise BusinessException(message="会话不存在", code=404)
    return conversation


def list_conversations(db: Session, user_id: int, limit: int = 50, offset: int = 0) -> list[Conversation]:
    """列出用户会话，最近活跃在前；用 id 作第二排序键保证翻页稳定。"""

    return (
        db.query(Conversation)
        .filter(
            Conversation.user_id == user_id,
            Conversation.deleted_at.is_(None),
        )
        .order_by(Conversation.update_time.desc(), Conversation.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )


def create_conversation(db: Session, user_id: int, title: str | None = None) -> Conversation:
    """新建会话；title 可为空，由首条消息自动生成。"""
    conversation = Conversation(user_id=user_id, title=title)
    db.add(conversation)
    db.commit()
    # commit 后属性过期，refresh 取回自增 id 和数据库默认值。
    db.refresh(conversation)
    return conversation


def update_conversation_title(
    db: Session, conversation_id: int, user_id: int, title: str
) -> Conversation:
    """重命名会话；长度和空值校验由 schema 负责。"""
    conversation = get_owned_conversation(db, conversation_id, user_id)
    conversation.title = title
    db.commit()
    db.refresh(conversation)
    # 结果页投影里带会话标题，改名后缓存要失效。
    cache.delete(cache.plan_key(conversation_id))
    return conversation


def delete_conversation(db: Session, conversation_id: int, user_id: int) -> None:
    """软删除会话：写 deleted_at，保留消息和运行记录。

    检查点不属于 conversations 表，需单独清理；清理失败只留下孤儿数据，
    不应影响已经提交的软删除。
    """
    conversation = get_owned_conversation(db, conversation_id, user_id)
    conversation.deleted_at = datetime.now(timezone.utc)
    db.commit()

    # commit 后清缓存，避免回滚时白清。
    cache.delete(
        cache.history_key(conversation_id),
        cache.plan_key(conversation_id),
    )

    # 尽力而为：内部吞异常，失败只留下孤儿检查点。
    delete_thread_for(conversation_id)
