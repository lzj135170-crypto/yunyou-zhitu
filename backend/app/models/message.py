from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# 只在类型检查阶段导入，运行时跳过，用于打断与 conversation.py 的循环导入
if TYPE_CHECKING:
    from app.models.conversation import Conversation


class Message(Base):
    """消息表：会话里的每一条消息，用户问的和 AI 答的各占一行。

    继承 Base 后自动拥有 id / create_time / update_time，
    其中 create_time 就是需求里的 created_at，用来还原消息的先后顺序。
    """

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')",
            name="ck_messages_role",
        ),
        # 消息窗口/分页都是 WHERE conversation_id=? ORDER BY id DESC，
        # 复合索引能同时覆盖过滤和排序，避免数据库再排一次序。
        Index("ix_messages_conversation_id_id", "conversation_id", "id"),
        {"comment": "消息表"},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="主键ID")

    # 所属会话ID。级联删除的含义：物理删掉一个会话时，它底下的所有消息自动被数据库清掉。
    # 业务删除是软删除，不会触发这个级联。
    # 不再单列 index=True：下面的复合索引已经以 conversation_id 开头。
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        comment="所属会话ID",
        nullable=False,
    )

    # 角色：这条消息是谁说的。约定取值——
    #   user      用户自己说的
    #   assistant AI 的回复
    #   system    系统提示词（用来给 AI 设定人设和规则，用户看不到）
    #   tool      工具调用返回的结果（比如查天气返回的原始 JSON）
    # 用普通字符串而不是数据库枚举类型，是为了以后加新角色不用改表结构。
    role: Mapped[str] = mapped_column(
        String(20),
        comment="角色: user-用户, assistant-AI回复, system-系统提示, tool-工具结果",
        nullable=False,
    )

    # 正文用 Text，长度不可控，不能用 String(n)。
    content: Mapped[str] = mapped_column(Text, comment="消息正文", nullable=False)

    # 产出的智能体名称；用户消息为空。
    agent_name: Mapped[str | None] = mapped_column(String(50), comment="智能体名称")

    # ORM 关系，不生成列；级联规则只在父侧声明，conversation_id 非空故类型非 Optional。
    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
