"""按景点顺序生成结果页路线。

只处理 plan.attractions 中真实存在的景点；缺坐标时用高德 POI 补。
每段按直线距离选择步行/骑行/打车，单段失败用直线估算兜底。
路线内容不落库、不写 Redis，遵守高德数据条款。
"""

import itertools
import logging
import math
import re
import time
import unicodedata
from dataclasses import dataclass, field

import httpx

from app.config import settings
from app.schemas.trip import (
    TripDayRoute,
    TripPlanResponse,
    TripRouteHotel,
    TripRouteLeg,
    TripRouteOption,
    TripRoutesRequest,
    TripRoutesResponse,
)
from app.services import amap_map

logger = logging.getLogger(__name__)

# 一条请求最多算几个景点。再多会产生大量 leg，个人 key 的 QPS 也扛不住。
MAX_ROUTE_ATTRACTIONS = 8

# 硬性护栏：一条请求最多算多少条腿、最多算多久。超出后剩余腿直接直线估算，
# 宁可给用户一个粗略结果，也不让接口在个人 key 的 QPS 下无限排队。
MAX_LEGS = 30
ROUTE_DEADLINE_SECONDS = 45.0

# 直线距离决定默认交通方式。阈值和 S11 的候选集合保持一致。
WALK_MAX_M = 1200
BIKE_MAX_M = 4000

# 时段顺序的"虚拟距离"：如果下午的景点排在上午前面，就加 3 公里成本。
# 只有绕路超过 3 公里时，才允许打乱 morning -> afternoon -> evening 的顺序。
SLOT_PENALTY_M = 3000

# 枚举全排列的上限。再多的景点用最近邻，避免 8! 级别的重复计算拖慢接口。
MAX_PERMUTATION_POINTS = 7

# 只有兜底估算才用到的平均速度（km/h）。
_MODE_SPEED_KMH = {
    "walking": 4.5,
    "bicycling": 12.0,
    "driving": 25.0,
    "transit": 18.0,
}

SLOT_ORDER = ("morning", "afternoon", "evening")

# 模型给的景点名经常带括号、斜杠或后缀："开封府 / 包公祠（开封老城区）"。
# 日程正文不会照抄这些修饰，必须拆成多个别名才能匹配上。
_NAME_SPLIT_RE = re.compile(r"[/／、+＋\-—,，·•]")
_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")
_SPACE_AROUND_SEP_RE = re.compile(r"\s*([/／、+＋\-—,，·•])\s*")
_CATEGORY_SUFFIXES = (
    "风景区",
    "博物院",
    "博物馆",
    "步行街",
    "老城区",
    "景区",
    "公园",
    "一带",
    "周边",
    "附近",
    "商圈",
)

# 打分权重。全名命中直接给 6；普通别名按长度分档。
_FULL_NAME_BONUS = 6
_ALIAS_BONUS_LONG = 3  # >= 4 字
_ALIAS_BONUS_MID = 2  # 3 字
_ALIAS_BONUS_SHORT = 1  # 2 字；单字别名在 normalize 阶段就丢掉
_BRACKET_BONUS = 4
_START_BONUS = 2
_START_WINDOW = 12

# 这些词出现在景点名附近，通常意味着"顺带/备选/避开/没去成"，不是当天主行程。
# 命中一次扣 4 分，抵消掉普通别名带来的加分。
_NEGATIVE_WORDS = (
    "避开",
    "闭馆",
    "如果",
    "若",
    "可以",
    "顺带",
    "路过",
    "附近",
    "周边",
    "补上",
    "没走够",
    "备选",
    "不推荐",
    "远观",
    "外观",
)
_NEGATIVE_WINDOW = 10
_NEGATIVE_PENALTY = 4


@dataclass(frozen=True)
class SlotScore:
    """一个景点在某天某个时段的匹配分。S08 用它决定景点归属哪一天。"""

    day: int
    slot: str
    slot_index: int
    score: int


@dataclass(frozen=True)
class _RoutePoint:
    name: str
    longitude: float
    latitude: float


@dataclass
class _RouteBuildState:
    """一次路线请求内共享的上下文：连接、城市编码、去重缓存和预算。"""

    client: httpx.Client
    key: str
    citycode: str
    deadline: float
    cache: dict[tuple, amap_map.RoutePath | None] = field(
        default_factory=dict
    )
    leg_count: int = 0
    deadline_warned: bool = False
    limit_warned: bool = False


def normalize_aliases(name: str) -> list[str]:
    """把一个景点名拆成可用于日程匹配的别名。

    返回的第一个元素约定为全名（`score_match` 会按 +6 处理）：
    "开封府 / 包公祠（开封老城区）" -> ["开封府/包公祠(开封老城区)", "开封府/包公祠",
    "开封府", "包公祠", ...]。
    """
    text = unicodedata.normalize("NFKC", name).strip()
    if not text:
        return []

    # 统一去掉分隔符两侧的空格，"开封古城墙 / 大梁门段" 才能匹配
    # 正文里的 "开封古城墙/大梁门段"。
    full = _SPACE_AROUND_SEP_RE.sub(r"\1", text)
    no_parens = _PAREN_RE.sub("", full).strip()

    candidates: list[str] = [full]
    if no_parens and no_parens != full:
        candidates.append(no_parens)

    for segment in _NAME_SPLIT_RE.split(no_parens or full):
        segment = segment.strip()
        if not segment:
            continue
        candidates.append(segment)
        stripped = _strip_category_suffix(segment)
        if stripped and stripped != segment:
            candidates.append(stripped)

    aliases: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        alias = candidate.strip()
        # 单字别名（"园""塔"）误伤太严重；两字别名按 +1 计，故宫/景山这类
        # 核心名只有两字，全靠它们才能在正文里出现。
        if len(alias) < 2 or alias in seen:
            continue
        if not any(ch.isalnum() for ch in alias):
            continue
        seen.add(alias)
        aliases.append(alias)
    return aliases


def score_match(day_text: str, aliases: list[str]) -> int:
    """给一段日程文字和一组别名打分。

    同一段文字里重复出现会重复计分；被更长别名覆盖的短别名不重复计分，
    避免"春熙路/太古里/IFS"同时按全名和三个分段各加一次。
    """
    if not day_text or not aliases:
        return 0

    text = unicodedata.normalize("NFKC", day_text)
    full_name = aliases[0]
    score = 0
    spans: list[tuple[int, int]] = []

    for alias in sorted(aliases, key=len, reverse=True):
        for start, end in _find_all(text, alias):
            if any(
                start < span_end and end > span_start
                for span_start, span_end in spans
            ):
                continue
            spans.append((start, end))

            if alias == full_name:
                score += _FULL_NAME_BONUS
            else:
                score += _alias_bonus(alias)

            if _inside_brackets(text, start, end):
                score += _BRACKET_BONUS
            if start <= _START_WINDOW:
                score += _START_BONUS
            if _has_negative_nearby(text, start, end):
                score -= _NEGATIVE_PENALTY

    return score


def score_attraction_days(
    plan: TripPlanResponse, name: str
) -> list[SlotScore]:
    """输出一个景点在每天/每个时段的得分，供 S08 分组使用。"""
    aliases = normalize_aliases(name)
    scores: list[SlotScore] = []
    for day in plan.days:
        for index, slot in enumerate(SLOT_ORDER):
            text = getattr(day, slot, "") or ""
            scores.append(
                SlotScore(
                    day=day.day,
                    slot=slot,
                    slot_index=index,
                    score=score_match(text, aliases),
                )
            )
    return scores


def _strip_category_suffix(segment: str) -> str:
    for suffix in _CATEGORY_SUFFIXES:
        if segment.endswith(suffix) and len(segment) > len(suffix):
            return segment[: -len(suffix)].strip()
    return ""


def _alias_bonus(alias: str) -> int:
    if len(alias) >= 4:
        return _ALIAS_BONUS_LONG
    if len(alias) == 3:
        return _ALIAS_BONUS_MID
    return _ALIAS_BONUS_SHORT


def _find_all(text: str, alias: str):
    start = 0
    while True:
        index = text.find(alias, start)
        if index == -1:
            return
        yield index, index + len(alias)
        start = index + 1


def _inside_brackets(text: str, start: int, end: int) -> bool:
    open_index = text.rfind("【", 0, start)
    if open_index == -1:
        return False
    # 【 和命中之间已经出现 】，说明这个括号早已闭合。
    if text.rfind("】", open_index, start) != -1:
        return False
    return text.find("】", end) != -1


def _has_negative_nearby(text: str, start: int, end: int) -> bool:
    window = text[
        max(0, start - _NEGATIVE_WINDOW) : min(
            len(text), end + _NEGATIVE_WINDOW
        )
    ]
    return any(word in window for word in _NEGATIVE_WORDS)


def build_routes(
    plan: TripPlanResponse, request: TripRoutesRequest
) -> TripRoutesResponse:
    """把请求里的景点先按日程分组，再逐天排序并生成路线。

    没有酒店时走开放路径；有酒店时固定首尾。组内顺序由直线距离 + 时段惩罚
    决定，真实路线只对最终顺序取一次。
    没有任何可用景点时抛 ValueError，由 API 层映射成 422。
    """
    started = time.monotonic()
    key = amap_map.require_amap_key()
    warnings: list[str] = []

    with httpx.Client(
        timeout=settings.AMAP_HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
    ) as client:
        points = _collect_points(plan, request, client, key, warnings)
        if not points:
            raise ValueError("没有可规划的景点")

        hotel = resolve_hotel(plan, request, client, key)
        hotel_point = (
            _RoutePoint(
                hotel.name or hotel.address or "酒店",
                hotel.longitude,
                hotel.latitude,
            )
            if hotel is not None
            else None
        )

        # transit 接口只认 citycode；解析不到就让公交候选直接跳过。
        citycode = amap_map.resolve_citycode(
            plan.destination, client=client, key=key
        )
        if not citycode and plan.destination.strip():
            warnings.append(
                "没有解析到目的地城市编码，公交/地铁方案未参与比较"
            )

        state = _RouteBuildState(
            client=client,
            key=key,
            citycode=citycode,
            deadline=started + ROUTE_DEADLINE_SECONDS,
        )

        assignments, grouping_warnings = group_attractions_by_day(plan, points)
        warnings.extend(grouping_warnings)

        day_routes: list[TripDayRoute] = []
        for day_number in sorted(assignments):
            day_points = assignments[day_number]
            day_warnings: list[str] = []

            slot_indices = {
                point.name: _day_slot_index(plan, point.name, day_number)
                for point in day_points
            }
            ordered_points = _order_points(
                day_points, hotel_point, slot_indices
            )

            # 有酒店时，每天第一腿从酒店出发、最后一腿回酒店。
            path = list(ordered_points)
            if hotel_point is not None and ordered_points:
                path = [hotel_point, *path, hotel_point]

            legs = [
                _build_leg(state, start, end, day_warnings)
                for start, end in zip(path, path[1:])
            ]

            if not day_points:
                day_warnings.append(
                    f"第 {day_number} 天没有可规划的景点，路线为空"
                )
            elif len(day_points) == 1 and hotel_point is None:
                day_warnings.append(
                    f"第 {day_number} 天只有一个景点，没有需要计算的路线"
                )

            day_routes.append(
                TripDayRoute(
                    day=day_number,
                    places=[point.name for point in ordered_points],
                    legs=legs,
                    total_distance_m=sum(
                        leg.recommended.distance_m for leg in legs
                    ),
                    total_duration_s=sum(
                        leg.recommended.duration_s for leg in legs
                    ),
                    total_cost_yuan=round(
                        sum(leg.recommended.cost_yuan for leg in legs), 1
                    ),
                    cost_is_estimate=any(
                        leg.recommended.cost_is_estimate
                        or leg.recommended.estimated
                        for leg in legs
                    ),
                    warnings=day_warnings,
                )
            )

    return TripRoutesResponse(
        days=day_routes, hotel=hotel, warnings=warnings
    )


def group_attractions_by_day(
    plan: TripPlanResponse, places: list[_RoutePoint]
) -> tuple[dict[int, list[_RoutePoint]], list[str]]:
    """把景点分到每天。

    规则：
    1. 每个景点取 S07 分数最高的一天作为主 day；
    2. 次高 day 达到主 day 的 70%、且当天文字里没有负向词，才保留（明确重访）；
    3. 所有景点都没匹配上 -> 按顺序连续均分；
    4. 只有部分没匹配 -> 按已分配景点的经纬度质心并入最近的一天。

    返回 (day -> 景点列表, warnings)。每个 day 内部保持传入顺序。
    """
    day_numbers = sorted({day.day for day in plan.days})
    if not day_numbers:
        return {1: list(places)}, ["行程没有按天拆分，已全部放在第 1 天"]

    assignments: dict[int, list[_RoutePoint]] = {
        day: [] for day in day_numbers
    }
    matched: list[_RoutePoint] = []
    unmatched: list[_RoutePoint] = []
    warnings: list[str] = []

    for point in places:
        aliases = normalize_aliases(point.name)
        scores = _day_scores(plan, point.name)
        ranked = sorted(
            ((day, score) for day, score in scores.items() if score > 0),
            key=lambda item: (-item[1], item[0]),
        )

        if not ranked:
            unmatched.append(point)
            continue

        primary_day, primary_score = ranked[0]
        if primary_day not in assignments:
            assignments[primary_day] = []
        assignments[primary_day].append(point)
        matched.append(point)

        if len(ranked) < 2:
            continue
        second_day, second_score = ranked[1]
        if second_score < primary_score * 0.7:
            continue
        if _day_has_negative_mention(plan, second_day, aliases):
            continue
        if second_day not in assignments:
            assignments[second_day] = []
        assignments[second_day].append(point)

    if not matched:
        # 一个都没匹配上时，质心法没有参考点，只能按顺序均分。
        assignments = _distribute_evenly(day_numbers, places)
        warnings.append(
            "没有景点能和每日行程文字对应，已按顺序平均分配到各天"
        )
        return assignments, warnings

    for point in unmatched:
        day = _nearest_day(point, assignments)
        assignments[day].append(point)
        warnings.append(
            f"「{point.name}」未在每日行程文字中出现，"
            f"已按位置并入第 {day} 天"
        )

    return assignments, warnings


def _day_scores(plan: TripPlanResponse, name: str) -> dict[int, int]:
    """把 S07 的时段分数合成每天一个分数，同一天取最强时段。"""
    scores: dict[int, int] = {}
    for slot in score_attraction_days(plan, name):
        scores[slot.day] = max(scores.get(slot.day, slot.score), slot.score)
    return scores


def _day_has_negative_mention(
    plan: TripPlanResponse, day_number: int, aliases: list[str]
) -> bool:
    for day in plan.days:
        if day.day != day_number:
            continue
        for slot in SLOT_ORDER:
            text = unicodedata.normalize(
                "NFKC", getattr(day, slot, "") or ""
            )
            for alias in aliases:
                for start, end in _find_all(text, alias):
                    if _has_negative_nearby(text, start, end):
                        return True
    return False


def _distribute_evenly(
    day_numbers: list[int], places: list[_RoutePoint]
) -> dict[int, list[_RoutePoint]]:
    assignments: dict[int, list[_RoutePoint]] = {
        day: [] for day in day_numbers
    }
    if not day_numbers or not places:
        return assignments

    base, extra = divmod(len(places), len(day_numbers))
    index = 0
    for position, day in enumerate(day_numbers):
        count = base + (1 if position < extra else 0)
        assignments[day] = list(places[index : index + count])
        index += count
    return assignments


def _nearest_day(
    point: _RoutePoint, assignments: dict[int, list[_RoutePoint]]
) -> int:
    """找已分配景点质心最近的一天。全部为空时退回第一天。"""
    best_day = next(iter(assignments), 1)
    best_distance: float | None = None

    for day, day_points in assignments.items():
        if not day_points:
            continue
        longitude = sum(p.longitude for p in day_points) / len(day_points)
        latitude = sum(p.latitude for p in day_points) / len(day_points)
        distance = _haversine_m(
            point.longitude, point.latitude, longitude, latitude
        )
        if best_distance is None or distance < best_distance:
            best_day = day
            best_distance = distance

    return best_day


def resolve_hotel(
    plan: TripPlanResponse,
    request: TripRoutesRequest,
    client: httpx.Client,
    key: str,
) -> TripRouteHotel | None:
    """解析用户输入的酒店。有坐标直接用，否则按地址地理编码。

    找不到地址时抛 ValueError（API 层映射 422），而不是静默退回无酒店——
    否则用户会以为路线是按酒店算的，实际却不是。
    """
    name = request.hotel_name.strip()
    address = request.hotel_address.strip()

    if (
        request.hotel_longitude is not None
        and request.hotel_latitude is not None
    ):
        return TripRouteHotel(
            name=name or "酒店",
            address=address,
            longitude=request.hotel_longitude,
            latitude=request.hotel_latitude,
        )

    query = address or name
    if not query:
        return None

    try:
        place = amap_map.geocode_address(
            query, plan.destination, client=client, key=key
        )
    except amap_map.AmapUpstreamError as e:
        logger.warning("解析酒店地址失败：%s infocode=%s", e, e.infocode)
        place = None

    if place is None:
        raise ValueError("没找到这个酒店地址，请补充城市或换一个地址")

    return TripRouteHotel(
        name=name or "酒店",
        address=address or query,
        longitude=place.longitude,
        latitude=place.latitude,
        citycode=place.citycode,
    )


def _day_slot_index(
    plan: TripPlanResponse, name: str, day_number: int
) -> int | None:
    """景点在这一天最可能属于哪个时段。分数非正或没匹配上返回 None。"""
    best: SlotScore | None = None
    for slot in score_attraction_days(plan, name):
        if slot.day != day_number:
            continue
        if best is None or slot.score > best.score:
            best = slot
    if best is None or best.score <= 0:
        return None
    return best.slot_index


def _order_points(
    points: list[_RoutePoint],
    hotel: _RoutePoint | None,
    slot_indices: dict[str, int | None],
) -> list[_RoutePoint]:
    """给一天的景点排序。

    优化目标是直线距离（不是真实路线，避免排列组合时打爆高德 QPS）加
    时段倒序惩罚。≤7 个景点枚举全排列，更多时用最近邻。
    """
    if len(points) <= 1:
        return list(points)
    if len(points) <= MAX_PERMUTATION_POINTS:
        return _order_by_permutation(points, hotel, slot_indices)
    return _nearest_neighbour_order(points, hotel, slot_indices)


def _order_by_permutation(
    points: list[_RoutePoint],
    hotel: _RoutePoint | None,
    slot_indices: dict[str, int | None],
) -> list[_RoutePoint]:
    best_order: list[_RoutePoint] | None = None
    best_cost: float | None = None

    for permutation in itertools.permutations(points):
        cost = _order_cost(permutation, hotel, slot_indices)
        if best_cost is None or cost < best_cost:
            best_cost = cost
            best_order = list(permutation)

    return best_order or list(points)


def _nearest_neighbour_order(
    points: list[_RoutePoint],
    hotel: _RoutePoint | None,
    slot_indices: dict[str, int | None],
) -> list[_RoutePoint]:
    """>7 个景点时的近似解。无酒店会尝试每个起点，避免第一个点决定全局。"""
    seeds: list[_RoutePoint | None] = (
        [None] if hotel is not None else list(points)
    )
    best_order: list[_RoutePoint] | None = None
    best_cost: float | None = None

    for seed in seeds:
        if seed is None:
            order: list[_RoutePoint] = []
            remaining = list(points)
            current = hotel
        else:
            order = [seed]
            remaining = [point for point in points if point is not seed]
            current = seed

        if current is None:
            continue

        current_slot = slot_indices.get(seed.name) if seed is not None else None
        while remaining:
            next_point = min(
                remaining,
                key=lambda candidate: _haversine_m(
                    current.longitude,
                    current.latitude,
                    candidate.longitude,
                    candidate.latitude,
                )
                + _slot_transition_penalty(
                    current_slot, slot_indices.get(candidate.name)
                ),
            )
            order.append(next_point)
            remaining.remove(next_point)
            current = next_point
            current_slot = slot_indices.get(next_point.name)

        cost = _order_cost(order, hotel, slot_indices)
        if best_cost is None or cost < best_cost:
            best_cost = cost
            best_order = order

    return best_order or list(points)


def _order_cost(
    order: tuple[_RoutePoint, ...] | list[_RoutePoint],
    hotel: _RoutePoint | None,
    slot_indices: dict[str, int | None],
) -> float:
    if not order:
        return 0.0

    cost = 0.0
    if hotel is not None:
        cost += _haversine_m(
            hotel.longitude,
            hotel.latitude,
            order[0].longitude,
            order[0].latitude,
        )

    for start, end in zip(order, order[1:]):
        cost += _haversine_m(
            start.longitude,
            start.latitude,
            end.longitude,
            end.latitude,
        )

    if hotel is not None:
        cost += _haversine_m(
            order[-1].longitude,
            order[-1].latitude,
            hotel.longitude,
            hotel.latitude,
        )

    # 时段倒序：下午排在上午前面、晚上排在下午前面，每次加 3 公里成本。
    for index, left in enumerate(order):
        left_slot = slot_indices.get(left.name)
        if left_slot is None:
            continue
        for right in order[index + 1 :]:
            right_slot = slot_indices.get(right.name)
            if right_slot is not None and left_slot > right_slot:
                cost += SLOT_PENALTY_M

    return cost


def _slot_transition_penalty(
    previous: int | None, upcoming: int | None
) -> float:
    if previous is None or upcoming is None:
        return 0.0
    return float(SLOT_PENALTY_M) if upcoming < previous else 0.0


def _collect_points(
    plan: TripPlanResponse,
    request: TripRoutesRequest,
    client: httpx.Client,
    key: str,
    warnings: list[str],
) -> list[_RoutePoint]:
    """把请求里的点对齐到行程景点，补坐标，按输入顺序返回。"""
    allowed = {
        item.name.strip()
        for item in plan.attractions
        if item.name.strip()
    }
    points: list[_RoutePoint] = []
    seen: set[str] = set()
    ignored: list[str] = []

    for item in request.places:
        name = item.name.strip()
        if not name or name in seen:
            continue
        if name not in allowed:
            ignored.append(name)
            continue
        if len(points) >= MAX_ROUTE_ATTRACTIONS:
            warnings.append(
                f"景点超过 {MAX_ROUTE_ATTRACTIONS} 个，"
                f"只计算前 {MAX_ROUTE_ATTRACTIONS} 个"
            )
            break
        seen.add(name)

        longitude, latitude = item.longitude, item.latitude
        if longitude is None or latitude is None:
            try:
                resolved = amap_map.search_place(
                    client, key, name, plan.destination
                )
            except amap_map.AmapUpstreamError as e:
                logger.warning(
                    "补充景点坐标失败：%s infocode=%s", e, e.infocode
                )
                resolved = None
            if resolved is None:
                warnings.append(f"没有找到「{name}」的坐标，已跳过")
                continue
            longitude, latitude = resolved.longitude, resolved.latitude

        points.append(_RoutePoint(name, longitude, latitude))

    if ignored:
        # 只列前三个，避免把整段请求原样回显。
        shown = "、".join(ignored[:3])
        suffix = "等" if len(ignored) > 3 else ""
        warnings.append(f"已忽略不在这次行程里的地点：{shown}{suffix}")

    return points


def _build_leg(
    state: _RouteBuildState,
    start: _RoutePoint,
    end: _RoutePoint,
    warnings: list[str],
) -> TripRouteLeg:
    straight_m = _haversine_m(
        start.longitude, start.latitude, end.longitude, end.latitude
    )
    preferred_mode = _candidate_modes(straight_m)[0][0]

    if state.leg_count >= MAX_LEGS:
        if not state.limit_warned:
            warnings.append(
                f"路段超过 {MAX_LEGS} 条，剩余路段已按直线估算"
            )
            state.limit_warned = True
        route = _estimate_route(start, end, preferred_mode, straight_m)
        alternatives: list[amap_map.RoutePath] = []
    elif _deadline_exceeded(state):
        if not state.deadline_warned:
            warnings.append(
                f"路线计算超过 {int(ROUTE_DEADLINE_SECONDS)} 秒，"
                "剩余路段已按直线估算"
            )
            state.deadline_warned = True
        route = _estimate_route(start, end, preferred_mode, straight_m)
        alternatives = []
    else:
        state.leg_count += 1
        routes = _fetch_leg_options(state, start, end, straight_m)
        if routes:
            route, alternatives = _pick_leg_route(routes)
        else:
            route = _estimate_route(start, end, preferred_mode, straight_m)
            alternatives = []
            warnings.append(
                f"「{start.name}」到「{end.name}」没有取到真实路线，"
                "已按直线估算"
            )

    return TripRouteLeg(
        from_name=start.name,
        to_name=end.name,
        from_longitude=start.longitude,
        from_latitude=start.latitude,
        to_longitude=end.longitude,
        to_latitude=end.latitude,
        recommended=_to_option(route),
        alternatives=[_to_option(item) for item in alternatives],
    )


def _candidate_modes(straight_m: float) -> tuple[list[str], list[str]]:
    """返回 (主候选, 全失败后的兜底候选)。

    中距离不用把 walking 也一起打一遍，只有骑行/公交都失败才补；
    长距离的 driving 本身就是候选，不需要额外兜底。
    """
    if straight_m <= WALK_MAX_M:
        return ["walking"], ["bicycling"]
    if straight_m <= BIKE_MAX_M:
        return ["bicycling", "transit"], ["walking"]
    return ["transit", "driving"], []


def _deadline_exceeded(state: _RouteBuildState) -> bool:
    return time.monotonic() >= state.deadline


def _fetch_leg_options(
    state: _RouteBuildState,
    start: _RoutePoint,
    end: _RoutePoint,
    straight_m: float,
) -> list[amap_map.RoutePath]:
    primary, fallbacks = _candidate_modes(straight_m)
    routes = _fetch_modes(state, start, end, primary)
    if not routes and fallbacks and not _deadline_exceeded(state):
        routes = _fetch_modes(state, start, end, fallbacks)
    return routes


def _fetch_modes(
    state: _RouteBuildState,
    start: _RoutePoint,
    end: _RoutePoint,
    modes: list[str],
) -> list[amap_map.RoutePath]:
    routes: list[amap_map.RoutePath] = []
    for mode in modes:
        # 已经拿到至少一个结果时，超时就别再打了；否则至少试一次。
        if routes and _deadline_exceeded(state):
            break
        route = _cached_fetch(state, start, end, mode)
        if route is not None:
            routes.append(route)
    return routes


def _cached_fetch(
    state: _RouteBuildState,
    start: _RoutePoint,
    end: _RoutePoint,
    mode: str,
) -> amap_map.RoutePath | None:
    cache_key = (
        mode,
        round(start.longitude, 6),
        round(start.latitude, 6),
        round(end.longitude, 6),
        round(end.latitude, 6),
    )
    if cache_key in state.cache:
        return state.cache[cache_key]

    try:
        route = amap_map.fetch_route(
            (start.longitude, start.latitude),
            (end.longitude, end.latitude),
            mode,
            client=state.client,
            key=state.key,
            city1=state.citycode if mode == "transit" else "",
            city2=state.citycode if mode == "transit" else "",
        )
    except amap_map.AmapUpstreamError as e:
        # 只记方式和错误码，不把带 key 的 URL 打出来。
        logger.warning("取路线失败（%s）：%s infocode=%s", mode, e, e.infocode)
        route = None

    state.cache[cache_key] = route
    return route


def _pick_leg_route(
    routes: list[amap_map.RoutePath],
) -> tuple[amap_map.RoutePath, list[amap_map.RoutePath]]:
    """“时间短、价格低”：耗时分钟 + 2×费用；同分先便宜后快。"""

    def rank(route: amap_map.RoutePath) -> tuple[float, float, int]:
        return (
            route.duration_s / 60.0 + 2.0 * route.cost_yuan,
            route.cost_yuan,
            route.duration_s,
        )

    ordered = sorted(routes, key=rank)
    return ordered[0], ordered[1:2]


def _estimate_route(
    start: _RoutePoint,
    end: _RoutePoint,
    mode: str,
    straight_m: float,
) -> amap_map.RoutePath:
    """拿不到真实路线时的兜底。

    直线距离乘 1.3 粗略补偿道路绕行，只用于展示量级；`estimated=True`
    让前端把这条线画成虚线并标注"估算"。
    """
    distance_m = max(1, int(round(straight_m * 1.3)))
    speed_kmh = _MODE_SPEED_KMH.get(mode, 4.5)
    duration_s = max(
        60, int(round(distance_m / 1000.0 / speed_kmh * 3600))
    )
    cost_yuan, cost_is_estimate = amap_map.estimate_route_cost(
        mode, distance_m, 0
    )

    return amap_map.RoutePath(
        mode=mode,
        mode_label=amap_map.ROUTE_MODES[mode][1],
        distance_m=distance_m,
        duration_s=duration_s,
        cost_yuan=cost_yuan,
        cost_is_estimate=cost_is_estimate,
        polyline=[
            [start.longitude, start.latitude],
            [end.longitude, end.latitude],
        ],
        summary="直线估算",
        estimated=True,
    )


def _to_option(route: amap_map.RoutePath) -> TripRouteOption:
    return TripRouteOption(
        mode=route.mode,
        mode_label=route.mode_label,
        distance_m=route.distance_m,
        duration_s=route.duration_s,
        cost_yuan=route.cost_yuan,
        cost_is_estimate=route.cost_is_estimate,
        polyline=route.polyline,
        summary=route.summary,
        estimated=route.estimated,
        tolls=route.tolls,
    )


def _haversine_m(
    longitude1: float, latitude1: float, longitude2: float, latitude2: float
) -> float:
    """两点球面距离（米）。只用来挑交通方式，不用于展示精确距离。"""
    earth_radius_m = 6371000.0
    lat1 = math.radians(latitude1)
    lat2 = math.radians(latitude2)
    delta_lat = lat2 - lat1
    delta_lng = math.radians(longitude2 - longitude1)
    a = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lng / 2) ** 2
    )
    return 2 * earth_radius_m * math.asin(math.sqrt(a))
