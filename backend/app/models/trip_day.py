from datetime import date as date_type
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.trip import Trip


class TripDay(Base):
    """行程中的一天：保存模型给出的上午/下午/晚上文字安排。"""

    __tablename__ = "trip_days"
    __table_args__ = (
        CheckConstraint("day_number > 0", name="ck_trip_days_day_number"),
        UniqueConstraint("trip_id", "day_number", name="uq_trip_days_trip_day"),
        {"comment": "行程每日安排表"},
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="主键ID")

    trip_id: Mapped[int] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"),
        comment="所属行程ID",
        nullable=False,
    )

    day_number: Mapped[int] = mapped_column(Integer, comment="第几天(1-based)", nullable=False)

    # 有 trip.start_date 时按 start_date + (day_number - 1) 推导；没有就留空。
    date: Mapped[date_type | None] = mapped_column(Date, comment="日期")

    morning: Mapped[str] = mapped_column(
        Text, comment="上午安排", nullable=False,
        default="", server_default="",
    )
    afternoon: Mapped[str] = mapped_column(
        Text, comment="下午安排", nullable=False,
        default="", server_default="",
    )
    evening: Mapped[str] = mapped_column(
        Text, comment="晚上安排", nullable=False,
        default="", server_default="",
    )
    summary: Mapped[str | None] = mapped_column(Text, comment="当天摘要")

    trip: Mapped["Trip"] = relationship(back_populates="days")
