from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# 仅类型检查时导入，避免 Conversation 与 Message/AgentRun 循环导入。
if TYPE_CHECKING:
    from app.models.agent_run import AgentRun
    from app.models.message import Message


class Conversation(Base):
    """会话表：一个用户与 AI 的一条对话；继承 Base 获得 id/create_time/update_time。"""

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'archived')", name="ck_conversations_status"
        ),
        CheckConstraint("message_count >= 0", name="ck_conversations_message_count"),
        # 会话列表按“我的 + 最近活跃”查，复合索引比单列 user_id 更贴合真实查询。
        Index("ix_conversations_user_update", "user_id", "update_time", "id"),
        # 软删除清理脚本会按 deleted_at 扫描，单独留一个索引。
        Index("ix_conversations_deleted_at", "deleted_at"),
        {"comment": "会话表"},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="主键ID")

    # 所属用户；users 表主键是 user_id。ondelete CASCADE 仅作防御清理，
    # 复合索引已以 user_id 开头，无需单列索引。
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        comment="所属用户ID",
        nullable=False,
    )

    # 会话标题，空白会话可为空。
    title: Mapped[str | None] = mapped_column(String(255), comment="会话标题")

    # active 正常、archived 归档；删除由 deleted_at 表示。
    status: Mapped[str] = mapped_column(
        String(20), comment="状态: active-正常, archived-归档",
        nullable=False, default="active", server_default="active",
    )

    # 最后一条消息时间，供列表排序；新会话可为空。
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="最后一条消息时间"
    )

    # 消息条数（不含 system），反规范化字段，避免列表页逐会话 COUNT。
    message_count: Mapped[int] = mapped_column(
        Integer, comment="消息条数(不含system)", nullable=False,
        default=0, server_default="0",
    )

    # 软删除时间；业务删除走这里，消息和运行记录保留。查询会话必须过滤 IS NULL。
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="软删除时间"
    )

    # 以下为 ORM 关系，不生成数据库列。
    # back_populates 必须与对端成对出现；cascade + passive_deletes 让物理删除
    # 交给数据库外键级联，避免逐条 SELECT/DELETE；order_by 用自增 id 保证顺序稳定。
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.id",
    )

    # 该会话下所有智能体的运行记录。软删除不会触发；物理清理时才级联删除。
    # 单独起一个 agent_runs 属性而不是叫 runs，是为了和表名 agent_runs 对上，
    # 看代码时不用在脑子里做一次名字映射。
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="AgentRun.id",
    )
