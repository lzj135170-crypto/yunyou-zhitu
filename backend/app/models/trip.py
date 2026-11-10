from datetime import date as date_type
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.trip_day import TripDay
    from app.models.trip_item import TripItem


class Trip(Base):
    """一次生成的行程单（头表）。

    它和 agent_runs 的关系：
    - agent_runs.output 是“模型这一轮到底产出了什么”的原始 JSONB 快照；
    - trips 是把这份产出投影成可查询、可列表、可后续编辑的业务实体。
    source_run_id 唯一，保证同一次运行重复投影不会产生两条行程。
    """

    __tablename__ = "trips"
    __table_args__ = (
        CheckConstraint("day_count > 0", name="ck_trips_day_count"),
        CheckConstraint(
            "traveler_count IS NULL OR traveler_count > 0",
            name="ck_trips_traveler_count",
        ),
        CheckConstraint(
            "budget_amount IS NULL OR budget_amount >= 0",
            name="ck_trips_budget_amount",
        ),
        CheckConstraint(
            "status IN ('draft', 'planned', 'ongoing', 'completed', 'cancelled')",
            name="ck_trips_status",
        ),
        CheckConstraint(
            "created_from IN ('agent', 'user', 'import')",
            name="ck_trips_created_from",
        ),
        CheckConstraint(
            "start_date IS NULL OR end_date IS NULL OR end_date >= start_date",
            name="ck_trips_dates",
        ),
        # PostgreSQL 的唯一约束允许多个 NULL；source_run_id 为空的手工行程可以有多条。
        UniqueConstraint("source_run_id", name="uq_trips_source_run_id"),
        Index("ix_trips_user_update", "user_id", "update_time", "id"),
        Index("ix_trips_conversation_id", "conversation_id"),
        Index("ix_trips_destination", "destination"),
        Index("ix_trips_start_date", "start_date"),
        Index("ix_trips_deleted_at", "deleted_at"),
        {"comment": "行程表"},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="主键ID")

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        comment="所属用户ID",
        nullable=False,
    )

    # 会话被物理清理时行程保留；软删除不影响这个外键。
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"),
        comment="来源会话ID",
    )
    # 同一次运行只投影一条行程；运行被清理时行程保留。
    source_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
        comment="来源运行ID",
    )

    title: Mapped[str] = mapped_column(
        String(255), comment="行程标题", nullable=False,
        default="", server_default="",
    )
    destination: Mapped[str] = mapped_column(
        String(100), comment="目的地", nullable=False,
        default="", server_default="",
    )

    # 用 date 而不是 datetime：行程是按天组织的，不需要时分秒。
    start_date: Mapped[date_type | None] = mapped_column(Date, comment="开始日期")
    end_date: Mapped[date_type | None] = mapped_column(Date, comment="结束日期")

    day_count: Mapped[int] = mapped_column(Integer, comment="天数", nullable=False)

    # 未知就是 NULL，不默认 1：把“没填”伪装成“1 人”会误导后续统计。
    traveler_count: Mapped[int | None] = mapped_column(Integer, comment="出行人数")

    budget_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), comment="预算金额"
    )
    currency: Mapped[str] = mapped_column(
        String(3), comment="币种", nullable=False,
        default="CNY", server_default="CNY",
    )
    pace: Mapped[str | None] = mapped_column(String(20), comment="节奏偏好")
    hotel_level: Mapped[str | None] = mapped_column(String(50), comment="住宿偏好")

    status: Mapped[str] = mapped_column(
        String(20),
        comment="状态: draft/planned/ongoing/completed/cancelled",
        nullable=False, default="draft", server_default="draft",
    )

    summary: Mapped[str | None] = mapped_column(Text, comment="行程概述")

    # 当次 TripPlanResponse 的完整 JSON 快照。agent_runs.output 是原始 findings，
    # 这里是投影后的、用户实际看到的版本；两者都保留，排查和展示互不依赖。
    plan_snapshot: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="结果页快照(JSON)"
    )

    created_from: Mapped[str] = mapped_column(
        String(20), comment="来源: agent/user/import", nullable=False,
        default="agent", server_default="agent",
    )

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="软删除时间"
    )

    days: Mapped[list["TripDay"]] = relationship(
        back_populates="trip",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TripDay.day_number",
    )
    items: Mapped[list["TripItem"]] = relationship(
        back_populates="trip",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="TripItem.sequence",
    )
