import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# 只在类型检查阶段导入，运行时跳过，用于打断与 conversation.py / message.py 的循环导入
if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.message import Message


class AgentRun(Base):
    """智能体运行记录表：智能体每被调用一次就写一行，用于事后排查问题和统计成本。

    这条记录和 messages 表的区别：
    - messages 存的是"给用户看的结果"
    - agent_runs 存的是"智能体内部怎么跑的"（喂进去什么参数、吐出来什么结构、跑了多久、烧了多少 token）
    排查线上问题时看这张表，比看聊天记录有用得多。
    """

    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'success', 'failed')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint("total_tokens >= 0", name="ck_agent_runs_total_tokens"),
        CheckConstraint("latency >= 0", name="ck_agent_runs_latency"),
        # trace_id 是外部排查用的稳定标识：日志、前端报错、DB 记录能串起来。
        UniqueConstraint("trace_id", name="uq_agent_runs_trace_id"),
        # 结果页按会话取最近运行；复合索引同时覆盖过滤和排序。
        Index("ix_agent_runs_conversation_id_id", "conversation_id", "id"),
        Index("ix_agent_runs_user_id_create_time", "user_id", "create_time"),
        Index("ix_agent_runs_status_create_time", "status", "create_time"),
        {"comment": "智能体运行记录表"},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="主键ID")

    # 这条运行记录属于哪个会话，方便按会话把"某次提问背后到底跑了哪些智能体"串起来看。
    # 不再单列 index=True：复合索引已经以 conversation_id 开头。
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        comment="所属会话ID",
        nullable=False,
    )

    # 冗余一份 user_id：按用户统计运行/成本时不必每次 join conversations。
    # 它是从会话归属推导出来的，不允许为空。
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        comment="所属用户ID",
        nullable=False,
    )

    # 这一轮的用户提问和 AI 回复。之前只能靠 input->>'message_id' 弱关联，
    # 回复还要靠“id 大于用户消息的第一条 assistant”猜；有了两个外键就能直接定位。
    # ondelete=SET NULL：消息被物理清理时，运行记录本身仍然保留。
    request_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        comment="本轮用户消息ID",
    )
    response_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        comment="本轮AI回复消息ID",
    )

    # 跑的哪个智能体。建索引是因为"查某个智能体的失败率/平均耗时"是很常见的运维查询。
    agent_name: Mapped[str] = mapped_column(
        String(50), comment="智能体名称", nullable=False, index=True
    )

    # 输入输出为什么用 JSONB 而不是普通 Text：
    # JSONB 是 PostgreSQL 专有的二进制 JSON 类型，写入时就把 JSON 解析成内部结构并去掉重复键，
    # 所以可以用 SQL 直接检索里面的字段，例如 WHERE input->>'city' = '北京'，
    # 还能针对某个字段单独建 GIN 索引做快速查找。
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="智能体输入参数(JSON)")
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="智能体输出结果(JSON)")

    # 运行状态。约定取值：running-执行中, success-成功, failed-失败。
    # 默认 running：记录是智能体开始跑之前先插入的，跑完了再回填状态和结果。
    status: Mapped[str] = mapped_column(
        String(20),
        comment="状态: running-执行中, success-成功, failed-失败",
        nullable=False,
        default="running",
        server_default="running",
    )

    # 本次调用消耗的 token 总数。拆成 prompt/completion 需要所有节点都上报，
    # 当前 LangGraph 只汇总总数，所以先保留 total_tokens，不编造拆分。
    total_tokens: Mapped[int] = mapped_column(
        Integer, comment="消耗的token总数", nullable=False,
        default=0, server_default="0",
    )

    # 耗时，单位毫秒。
    # 用整数存毫秒而不是用浮点数存秒：一是避免小数运算的精度问题，
    # 二是前端拿到后除以 1000 就能直接展示成秒，换算成本最低。
    latency: Mapped[int] = mapped_column(
        Integer, comment="耗时(毫秒)", nullable=False,
        default=0, server_default="0",
    )

    # 稳定追踪 ID。新运行用 uuid4().hex；旧数据迁移时用 md5 回填。
    trace_id: Mapped[str] = mapped_column(
        String(64),
        comment="运行追踪ID",
        nullable=False,
        default=lambda: uuid.uuid4().hex,
    )

    # 运行结束时间（成功或失败）。create_time 是开始时间，latency 是耗时。
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="运行结束时间"
    )

    # 失败时只记“异常类名 + 安全文案”，不写异常原文：
    # 异常信息里可能带数据库连接串、外部 API key 或本机路径。
    error_code: Mapped[str | None] = mapped_column(String(50), comment="错误码/异常类名")
    error_message: Mapped[str | None] = mapped_column(
        String(500), comment="对用户安全的错误摘要"
    )

    # ORM 关系（不是数据库字段，不会生成列）。
    conversation: Mapped["Conversation"] = relationship(back_populates="agent_runs")
    request_message: Mapped["Message | None"] = relationship(
        foreign_keys=[request_message_id]
    )
    response_message: Mapped["Message | None"] = relationship(
        foreign_keys=[response_message_id]
    )
