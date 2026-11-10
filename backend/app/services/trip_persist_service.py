"""把一次 AgentRun 的行程产出投影成 trips / trip_days / trip_items。

设计边界：
- agent_runs.output 永远保留“模型原始产出”，本服务只做派生投影；
- 同一个 source_run_id 最多一条 trip，重复调用是幂等的；
- 高德返回的坐标/POI/路线内容不落库，这里只写模型文本和用户输入；
- 投影失败不能影响聊天主流程：调用方负责捕获异常。
"""

import logging
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models.agent_run import AgentRun
from app.models.conversation import Conversation
from app.models.trip import Trip
from app.models.trip_day import TripDay
from app.models.trip_item import TripItem
from app.services import trip_route_service, trip_service

logger = logging.getLogger(__name__)


def sync_run_trip(db: Session, run_id: int, *, force: bool = False) -> str:
    """把一个运行投影成行程。返回 created/updated/skipped。

    ⚠️ 调用前不要在同一 session 里留未提交的业务写入；本函数会 commit。
    失败时 rollback 并抛出，由调用方决定是否吞掉。
    """
    run = db.get(AgentRun, run_id)
    if run is None:
        return "skipped"
    if run.status != "success" or not has_itinerary(run.output):
        return "skipped"

    conversation = db.get(Conversation, run.conversation_id)
    if conversation is None or conversation.deleted_at is not None:
        return "skipped"

    existing = (
        db.query(Trip).filter(Trip.source_run_id == run.id).first()
    )
    if existing is not None:
        # 软删除过的行程不复活；不 force 时也不重建。
        if existing.deleted_at is not None or not force:
            return "skipped"

    try:
        plan = trip_service.build_plan_for_run(db, conversation, run)
        if not plan.days:
            return "skipped"

        created = existing is None
        trip = existing or Trip(
            user_id=run.user_id,
            conversation_id=conversation.id,
            source_run_id=run.id,
            title="",
            destination="",
            day_count=1,
            status="planned",
            created_from="agent",
        )

        _fill_trip(trip, plan)
        if created:
            db.add(trip)
            db.flush()
        else:
            # 重建子表前先删干净，避免 (trip_id, day_number)/(trip_id, sequence)
            # 的唯一约束撞上旧数据。先删 items 再删 days。
            db.query(TripItem).filter(TripItem.trip_id == trip.id).delete(
                synchronize_session=False
            )
            db.query(TripDay).filter(TripDay.trip_id == trip.id).delete(
                synchronize_session=False
            )
            db.flush()

        day_map = _add_days(db, trip, plan)
        _add_items(db, trip, plan, day_map)
        db.commit()
        return "created" if created else "updated"
    except Exception:
        db.rollback()
        raise


def has_itinerary(output) -> bool:
    if not isinstance(output, dict):
        return False
    findings = output.get("findings")
    if not isinstance(findings, dict):
        return False
    itinerary = findings.get("itinerary")
    if not isinstance(itinerary, dict):
        return False
    days = itinerary.get("days")
    return isinstance(days, list) and bool(days)


def _fill_trip(trip: Trip, plan) -> None:
    day_count = len(plan.days)
    start_date = _parse_date(plan.start_date)
    end_date = _parse_date(plan.end_date)
    if start_date is not None and (end_date is None or end_date < start_date):
        # 模型偶尔给出早于开始日期的结束日期；按天数重算，避免 CheckConstraint
        # 让整条投影失败。没有开始日期时保持 NULL，不猜。
        end_date = start_date + timedelta(days=day_count - 1)

    destination = _text(plan.destination, 100)
    fallback_title = (
        f"{destination} {day_count} 天行程" if destination else f"{day_count} 天行程"
    )

    # 更新时保留用户可能改过的标题/状态；只刷新来源字段和快照。
    if not trip.title:
        plan_title = _text(plan.title, 255)
        if not plan_title or plan_title == "未命名行程":
            plan_title = fallback_title
        trip.title = plan_title
    if not trip.destination:
        trip.destination = destination
    trip.conversation_id = trip.conversation_id or plan.conversation_id
    if not trip.user_id:
        raise ValueError("trip.user_id 不能为空")

    trip.start_date = start_date
    trip.end_date = end_date
    trip.day_count = day_count
    trip.traveler_count = plan.travelers if (plan.travelers or 0) > 0 else None
    trip.budget_amount = _decimal(plan.budget)
    trip.currency = "CNY"
    trip.pace = _text(plan.pace, 20) or None
    trip.hotel_level = _text(plan.hotel_level, 50) or None
    trip.summary = plan.overview or None
    trip.plan_snapshot = plan.model_dump(mode="json")
    if not trip.status:
        trip.status = "planned"


def _add_days(db: Session, trip: Trip, plan) -> dict[int, TripDay]:
    """写入 trip_days，返回“模型原始 day 号 -> TripDay”映射。"""
    used: set[int] = set()
    original_to_final: dict[int, int] = {}
    day_map: dict[int, TripDay] = {}
    start_date = trip.start_date

    for index, day in enumerate(plan.days, start=1):
        number = day.day if isinstance(day.day, int) else index
        if number < 1 or number in used:
            number = index
            while number in used:
                number += 1
        used.add(number)
        original_to_final.setdefault(day.day, number)

        row = TripDay(
            trip_id=trip.id,
            day_number=number,
            date=(
                start_date + timedelta(days=number - 1)
                if start_date is not None
                else None
            ),
            morning=day.morning or "",
            afternoon=day.afternoon or "",
            evening=day.evening or "",
            summary=None,
        )
        db.add(row)
        day_map[number] = row

    db.flush()
    # 把原始 day 号也映射到同一个对象，供景点匹配使用。
    for original, final in original_to_final.items():
        day_map.setdefault(original, day_map[final])
    return day_map


def _add_items(db: Session, trip: Trip, plan, day_map: dict[int, TripDay]) -> None:
    sequence = 1

    for attraction in plan.attractions:
        title = _text(attraction.name, 255)
        if not title:
            continue

        slot = None
        trip_day_id = None
        scores = trip_route_service.score_attraction_days(plan, attraction.name)
        ranked = [s for s in scores if s.score > 0]
        if ranked:
            best = max(ranked, key=lambda s: (s.score, -s.day, -s.slot_index))
            slot = best.slot
            day_row = day_map.get(best.day)
            if day_row is not None:
                trip_day_id = day_row.id

        meta = {}
        if attraction.hours is not None:
            meta["hours"] = attraction.hours

        db.add(
            TripItem(
                trip_id=trip.id,
                trip_day_id=trip_day_id,
                sequence=sequence,
                item_type="attraction",
                title=title,
                description=attraction.reason or None,
                slot=slot,
                price_text=_text(attraction.price_range, 100) or None,
                duration_minutes=(
                    int(round(attraction.hours * 60))
                    if attraction.hours and attraction.hours > 0
                    else None
                ),
                currency="CNY",
                source="agent",
                meta=meta or None,
            )
        )
        sequence += 1

    for food in plan.foods:
        title = _text(food.name, 255)
        if not title:
            continue
        meta = _compact({
            "category": food.category,
            "meal_time": food.meal_time,
        })
        db.add(
            TripItem(
                trip_id=trip.id,
                sequence=sequence,
                item_type="food",
                title=title,
                description=food.reason or None,
                slot=_slot_from_meal_time(food.meal_time),
                price_text=_text(food.price_range, 100) or None,
                address=_text(food.area, 500) or None,
                currency="CNY",
                source="agent",
                meta=meta,
            )
        )
        sequence += 1

    for hotel in plan.hotels:
        title = _text(hotel.area, 255)
        if not title:
            continue
        meta = _compact({
            "level": hotel.level,
            "suitable_for": hotel.suitable_for,
        })
        db.add(
            TripItem(
                trip_id=trip.id,
                sequence=sequence,
                item_type="hotel",
                title=title,
                description=hotel.reason or None,
                price_text=_text(hotel.price_range, 100) or None,
                address=_text(hotel.area, 500) or None,
                currency="CNY",
                source="agent",
                meta=meta,
            )
        )
        sequence += 1

    for transport in plan.transport:
        title = _text(transport.mode, 255)
        if not title:
            continue
        meta = _compact({
            "duration": transport.duration,
            "best_for": transport.best_for,
        })
        db.add(
            TripItem(
                trip_id=trip.id,
                sequence=sequence,
                item_type="transport",
                title=title,
                description=transport.note or None,
                price_text=_text(transport.price_range, 100) or None,
                currency="CNY",
                source="agent",
                meta=meta,
            )
        )
        sequence += 1


def _slot_from_meal_time(value: str | None) -> str | None:
    text = value or ""
    if "早" in text:
        return "morning"
    if "午" in text:
        return "afternoon"
    if "晚" in text or "夜" in text:
        return "evening"
    return None


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except (TypeError, ValueError):
        return None


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _text(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _compact(data: dict) -> dict | None:
    cleaned = {}
    for key, value in data.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        cleaned[key] = value
    return cleaned or None
