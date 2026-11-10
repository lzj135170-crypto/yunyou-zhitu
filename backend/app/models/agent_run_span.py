from datetime import datetime
from typing import Any

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
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentRunSpan(Base):
    """智能体运行 span 明细表：一次 agent_run 内部的节点/LLM/外部调用树。

    和 agent_runs 的分工：
    - agent_runs 是一次对话的运行级摘要（一行）
    - agent_run_spans 是这次运行内部的明细（N 行），通过 run_id 挂回去

    这样既可以用 trace_id 查完整链路，也可以按 node_name/status 聚合慢节点。
    """

    __tablename__ = "agent_run_spans"
    __table_args__ = (
        CheckConstraint(
            "status IN ('running', 'success', 'failed', 'skipped')",
            name="ck_agent_run_spans_status",
        ),
        CheckConstraint("duration_ms >= 0", name="ck_agent_run_spans_duration"),
        CheckConstraint("tokens >= 0", name="ck_agent_run_spans_tokens"),
        UniqueConstraint(
            "run_id", "span_id", name="uq_agent_run_spans_run_span"
        ),
        Index(
            "ix_agent_run_spans_trace_start", "trace_id", "start_time"
        ),
        Index("ix_agent_run_spans_run_start", "run_id", "start_time"),
        Index(
            "ix_agent_run_spans_node_status", "node_name", "status"
        ),
        Index("ix_agent_run_spans_create_time", "create_time"),
        {"comment": "智能体运行 span 明细表"},
    )

    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True, comment="主键ID"
    )

    # 所属运行。运行被删除时 span 一起清掉；span 是运行的派生物。
    run_id: Mapped[int] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属运行ID",
    )

    # 32 位十六进制，和 agent_runs.trace_id 对齐；用于一次链路查询。
    trace_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="追踪ID"
    )
    # 16 位十六进制，按 OpenTelemetry 的 span_id 语义设计，方便以后接 OTLP。
    span_id: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="Span ID"
    )
    parent_span_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="父Span ID"
    )

    name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Span名称"
    )
    span_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        comment="类型: run/graph/node/llm/mcp/db/cache/rag/http",
    )
    node_name: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="节点名"
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="running",
        server_default="running",
        comment="状态: running/success/failed/skipped",
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, comment="开始时间"
    )
    end_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="结束时间"
    )
    duration_ms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="耗时(毫秒)",
    )
    # LLM span 存单次调用的 token；node span 存该节点的聚合值。
    # 查询统计时只取其中一种，不能相加，否则会重复计数。
    tokens: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="token用量",
    )

    error_code: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="错误码"
    )
    error_message: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="安全错误摘要"
    )

    # 只存小对象：provider/model/backend/rows/http_status/cache_hit 等。
    # 不存 prompt、回复正文、RAG context、带 key 的完整 URL。
    attributes: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True, comment="附加属性(JSON)"
    )

    # create_time / update_time 继承自 Base
