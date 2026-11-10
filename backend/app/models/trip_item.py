from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
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
    from app.models.trip import Trip
    from app.models.trip_day import TripDay


class TripItem(Base):
    """行程里的一个条目：景点、美食、住宿区域、交通方案或备注。

    trip_day_id 允许为空：景点能通过日程文字匹配到某天时绑定，
    美食/住宿/交通建议通常只是推荐，不强行塞进某一天。
    """

    __tablename__ = "trip_items"
    __table_args__ = (
        CheckConstraint("sequence >= 0", name="ck_trip_items_sequence"),
        CheckConstraint(
            "item_type IN ('attraction', 'food', 'hotel', 'transport', 'note')",
            name="ck_trip_items_type",
        ),
        CheckConstraint(
            "slot IS NULL OR slot IN ('morning', 'afternoon', 'evening')",
            name="ck_trip_items_slot",
        ),
        CheckConstraint(
            "price_amount IS NULL OR price_amount >= 0",
            name="ck_trip_items_price_amount",
        ),
        CheckConstraint(
            "duration_minutes IS NULL OR duration_minutes >= 0",
            name="ck_trip_items_duration",
        ),
        CheckConstraint(
            "source IN ('agent', 'user')", name="ck_trip_items_source"
        ),
        UniqueConstraint("trip_id", "sequence", name="uq_trip_items_trip_sequence"),
        Index("ix_trip_items_trip_day_slot_sequence", "trip_day_id", "slot", "sequence"),
        Index("ix_trip_items_trip_type", "trip_id", "item_type"),
        {"comment": "行程条目表"},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="主键ID")

    trip_id: Mapped[int] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"),
        comment="所属行程ID",
        nullable=False,
    )
    # 删除某一天时，绑在该天的条目跟着删；未绑天的推荐条目不受影响。
    trip_day_id: Mapped[int | None] = mapped_column(
        ForeignKey("trip_days.id", ondelete="CASCADE"),
        comment="所属天ID",
    )

    sequence: Mapped[int] = mapped_column(
        Integer, comment="行程内顺序", nullable=False,
        default=0, server_default="0",
    )
    item_type: Mapped[str] = mapped_column(String(20), comment="条目类型", nullable=False)
    title: Mapped[str] = mapped_column(String(255), comment="标题/名称", nullable=False)
    description: Mapped[str | None] = mapped_column(Text, comment="说明/推荐理由")

    slot: Mapped[str | None] = mapped_column(
        String(20), comment="时段: morning/afternoon/evening"
    )
    price_text: Mapped[str | None] = mapped_column(String(100), comment="价格原文")
    # 不从“约 80-120 元”这类自由文本里猜数字；宁可为空，也不伪造金额。
    price_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), comment="价格金额(可解析时)"
    )
    currency: Mapped[str] = mapped_column(
        String(3), comment="币种", nullable=False,
        default="CNY", server_default="CNY",
    )
    address: Mapped[str | None] = mapped_column(String(500), comment="地址/区域")
    duration_minutes: Mapped[int | None] = mapped_column(
        Integer, comment="建议时长(分钟)"
    )

    source: Mapped[str] = mapped_column(
        String(20), comment="来源: agent/user", nullable=False,
        default="agent", server_default="agent",
    )

    # 原始结构化字段（小时数、菜系、住宿档次、交通时长等），避免为每个类型加列。
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, comment="扩展信息(JSON)")

    trip: Mapped["Trip"] = relationship(back_populates="items")
    day: Mapped["TripDay | None"] = relationship(foreign_keys=[trip_day_id])
