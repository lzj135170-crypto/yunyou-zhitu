"""高德 Web 服务封装：静态地图、POI 搜索、住宿区域检索。

所有请求由后端代理并共用进程级限流器；Web 服务 key 不能下发前端。
高德条款禁止缓存地图内容：这里不落库、不写 Redis，只实时转发，
日志不记录带 key 的完整 URL。
"""

import logging
import math
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Iterable

import httpx

from app.common.resilience import (
    BreakerOutcome,
    CircuitOpenError,
    get_breaker,
)
from app.config import settings

logger = logging.getLogger(__name__)

# ⚠️ httpx 在 INFO 级别会把【完整请求 URL】打出来，而高德的 key 就在 query 里。
# 只要有人把日志级别调到 INFO，key 就会进日志。这里直接压掉它的请求日志。
# 我们自己的 warning 只记 infocode / 状态码，不记 URL。
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

PLACE_URL = "https://restapi.amap.com/v5/place/text"
GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
DISTRICT_URL = "https://restapi.amap.com/v3/config/district"
STATIC_MAP_URL = "https://restapi.amap.com/v3/staticmap"
DIRECTION_URL = "https://restapi.amap.com/v5/direction"

# 静态地图最多标几个点。结果页的景点卡一般 3-6 个，再多地图上会糊成一片。
MAX_MAP_POINTS = 6

# 每个住宿区域返回几家酒店。2-3 家够用户横向比较，再多既拖慢 QPS 也占页面。
HOTELS_PER_AREA = 3
# 一次多取几家再按评分排序，避免"高德返回前 5 家"里恰好没有高分酒店。
# page_size 不影响 QPS（仍然只发一次请求），只是让候选池大一点。
HOTEL_SEARCH_PAGE_SIZE = 10

# Agent 专家用的通用 POI 检索一次最多返回多少条。
# 10 条够模型挑 3-6 个景点/餐厅；再多多半也进不了上下文预算。
POI_MAX_LIMIT = 10

# 一条腿的折线最多留多少个点。高德会把每个转弯点都吐出来，长路线能上千个；
# 前端画线不需要这么密，降采样后既能看清走向，也不会把响应体撑大。
MAX_ROUTE_POLYLINE_POINTS = 600

# 打车费用的估算规则（不是实时计价）：
# 起步价 10 元含 2 公里，超出部分按 2.5 元/公里，再加过路费。
TAXI_BASE_YUAN = 10.0
TAXI_BASE_KM = 2.0
TAXI_PER_KM_YUAN = 2.5

# 共享单车按一次骑行 1.5 元估算。不同城市/品牌有差异，前端会标注"估算"。
BICYCLE_COST_YUAN = 1.5

ROUTE_MODES = {
    "walking": ("walking", "步行"),
    "bicycling": ("bicycling", "共享单车"),
    "driving": ("driving", "打车"),
    "transit": ("transit/integrated", "公交地铁"),
}

# transit 接口的 city1/city2 只认 citycode（成都 028），传城市名会 INVALID_PARAMS。

# 任意两次高德上游请求之间的最小间隔。个人 key 的 QPS 很低，实测 4 路并发
# 就会直接返回 `CUQPS_HAS_EXCEEDED_THE_LIMIT`（infocode=10021）。
#
# 所有出口（POI 搜索、地理编码、静态地图、以后的路线）共用同一个进程级节流器，
# 这样 /media、/hotels、/routes 同时被打开时也不会把 key 打爆。
# 注意：这是【进程内】限流；如果以后用多个 uvicorn worker 部署，每个进程会
# 各有一份节奏，届时需要换成跨进程的令牌桶（Redis 等）。
AMAP_CALL_INTERVAL_SECONDS = 0.5

_AMAP_SLOT_LOCK = threading.Lock()
_next_amap_call_at = 0.0


def _wait_for_amap_slot() -> None:
    """预订下一个全局调用时隙，并等到它。

    用"预订"而不是"持锁 sleep"：调用方算出自己的时隙后立刻释放锁，
    等待期间不阻塞其他线程。时间基准用 monotonic，不受系统时间跳变影响。
    """
    global _next_amap_call_at
    with _AMAP_SLOT_LOCK:
        slot = max(time.monotonic(), _next_amap_call_at)
        _next_amap_call_at = slot + AMAP_CALL_INTERVAL_SECONDS
    delay = slot - time.monotonic()
    if delay > 0:
        time.sleep(delay)


# 被 QPS 限流后等多久再试。只重试一次，再失败就跳过这个点。
QPS_RETRY_DELAY_SECONDS = 0.8

# 静态地图尺寸。scale=2 会返回 2 倍图，在高分屏上不糊。
MAP_WIDTH = 720
MAP_HEIGHT = 400
MAP_SCALE = 2

# 高德对静态地图 markers 的单组标签支持 A-Z 这类单字符。
_LABELS = "ABCDEFGH"


class AmapNotConfigured(RuntimeError):
    """没有配 key，或地图开关被关掉。"""


class AmapUpstreamError(RuntimeError):
    """高德接口返回了错误 / 网络失败。"""

    def __init__(
        self,
        message: str,
        *,
        infocode: str = "",
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.infocode = infocode
        # 保留 HTTP 状态码给熔断器区分 4xx 配置错误和 5xx/网络故障。
        # 不写进日志字符串，调用方只记 infocode/状态码。
        self.http_status = http_status


def require_amap_key() -> str:
    """读取并校验高德 key。没配置时抛 AmapNotConfigured，由 API 层映射 404。"""
    if not settings.AMAP_MAP_ENABLED:
        raise AmapNotConfigured("地图功能已关闭")
    key = settings.AMAP_WEB_SERVICE_KEY.strip()
    if not key:
        raise AmapNotConfigured("未配置高德 Web 服务 key")
    return key


@dataclass(frozen=True)
class MapPoint:
    """一个已经拿到坐标的点位。坐标是 GCJ-02，只在本进程内使用。"""

    name: str
    longitude: float
    latitude: float


@dataclass(frozen=True)
class PlaceMedia:
    """一个景点的坐标 + 高德 POI 图 + 地址。

    `photo_url` 可能为空（有些 POI 没有图），前端会退回渐变色块。
    URL 是公开 CDN 地址，不含 key。
    """

    name: str
    longitude: float
    latitude: float
    photo_url: str = ""
    address: str = ""


@dataclass(frozen=True)
class GeocodePlace:
    """一个地址/POI 解析出来的坐标。citycode 供公交路线使用（如成都 028）。"""

    longitude: float
    latitude: float
    citycode: str = ""
    formatted_address: str = ""


@dataclass(frozen=True)
class HotelAreaSpec:
    """调用方给的一个住宿区域。level 用来选搜索关键词的类型词。

    单独定义这个轻量类型，是为了让服务层不依赖 API 层的 Pydantic schema：
    S03 只需要把 `TripHotel` 映射成 (area, level) 即可。
    """

    area: str
    level: str = ""


@dataclass(frozen=True)
class HotelPlace:
    """高德检索到的一家真实酒店。

    ⚠️ 这里没有房价和房态：高德不提供实时房价，`avg_cost` 只是 POI 上的
    人均/参考消费，缺失时保持空字符串，前端不能拿它当报价。
    """

    area: str
    name: str
    longitude: float
    latitude: float
    address: str = ""
    photo_url: str = ""
    rating: float | None = None
    category: str = ""
    business_area: str = ""
    avg_cost: str = ""


@dataclass(frozen=True)
class HotelTierResult:
    """住宿区域里的一个档位：舒适型 / 经济型·民宿。

    每个档位单独检索、单独按评分排序，前端用 tab 切换。
    """

    key: str
    label: str
    places: list[HotelPlace] = field(default_factory=list)
    error: str = ""


@dataclass(frozen=True)
class HotelAreaResult:
    """一个住宿区域的检索结果。单区域失败只影响自己，其他区域照常返回。"""

    index: int
    area: str
    level: str = ""
    places: list[HotelPlace] = field(default_factory=list)
    error: str = ""
    tiers: list[HotelTierResult] = field(default_factory=list)


@dataclass(frozen=True)
class RestaurantPlace:
    """高德检索到的一家真实餐厅。

    和酒店一样，这里没有实时排队、订座和菜单价格。`avg_cost` 只是 POI 上的
    参考人均，缺失时保持空字符串，前端不能当成报价。
    """

    name: str
    longitude: float
    latitude: float
    address: str = ""
    # 仅用于校验高德是否忽略了 region；展示层不使用。
    cityname: str = ""
    adname: str = ""
    photo_url: str = ""
    rating: float | None = None
    category: str = ""
    business_area: str = ""
    avg_cost: str = ""


@dataclass(frozen=True)
class PoiFact:
    """高德检索到的一个真实 POI，给 Agent 专家当事实依据。

    和 `RestaurantPlace` / `HotelPlace` 的区别：这里不预设业务类型，
    景点、餐厅、酒店都用同一张表。`avg_cost` 同样只是 POI 上的参考人均，
    缺失时是空字符串，不能当报价；高德不提供门票和营业时间。
    """

    name: str
    address: str = ""
    cityname: str = ""
    adname: str = ""
    type: str = ""
    typecode: str = ""
    longitude: float = 0.0
    latitude: float = 0.0
    tel: str = ""
    rating: float | None = None
    business_area: str = ""
    avg_cost: str = ""


@dataclass(frozen=True)
class RoutePath:
    """两个坐标之间的一种陆路交通方式。

    `polyline` 是 [[lng, lat], ...]，GCJ-02，和地图一致。
    `cost_yuan` 是参考费用：步行恒为 0，骑行/打车是估算，见 `cost_is_estimate`。
    `estimated=True` 只在调用方拿不到真实路线、用直线兜底时出现。
    """

    mode: str
    mode_label: str
    distance_m: int
    duration_s: int
    cost_yuan: float = 0.0
    cost_is_estimate: bool = False
    polyline: list[list[float]] = field(default_factory=list)
    summary: str = ""
    estimated: bool = False
    tolls: int = 0


def render_static_map(destination: str, place_names: Iterable[str]) -> bytes:
    """只把目的地和景点渲染成静态地图 PNG（/map.png 用）。"""
    png, _ = render_plan_media(destination, place_names)
    return png


def render_plan_media(
    destination: str, place_names: Iterable[str]
) -> tuple[bytes, list[PlaceMedia]]:
    """一次解析景点，返回 (静态地图 PNG, 景点坐标+图片+地址)。

    结果页同时需要地图和景点图，分两个接口各查一遍会浪费 QPS，
    还可能撞上个人的并发限制。这里一次 POI 搜索同时拿到坐标和照片。
    """
    key = require_amap_key()

    names = _dedupe_names(place_names)[:MAX_MAP_POINTS]

    with httpx.Client(
        timeout=settings.AMAP_HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
    ) as client:
        places = _resolve_places(client, key, destination, names)
        points = [
            MapPoint(place.name, place.longitude, place.latitude) for place in places
        ]

        # 节流在 _fetch_static_map 内部统一做，这里不再手工 sleep。
        try:
            png = _fetch_static_map(client, key, points)
        except AmapUpstreamError as e:
            if e.infocode != "10021":
                raise
            # 静态图这一步也可能撞上 QPS，等一会儿再试一次。
            time.sleep(QPS_RETRY_DELAY_SECONDS)
            png = _fetch_static_map(client, key, points)

    return png, places


def _resolve_places(
    client: httpx.Client, key: str, destination: str, names: list[str]
) -> list[PlaceMedia]:
    """串行解析景点。

    调用节奏由 `_wait_for_amap_slot()` 在 HTTP 层统一控制，这里只管业务：
    连续两次被限流就停下（说明不是单个点的问题），否则继续。
    """
    places: list[PlaceMedia] = []
    consecutive_qps_failures = 0

    for name in names:
        try:
            place = search_place(client, key, name, destination)
        except AmapUpstreamError as e:
            if e.infocode == "10021":
                consecutive_qps_failures += 1
                # 连续被限流说明不是单个点的问题，继续打只会更糟。
                if consecutive_qps_failures >= 2:
                    logger.warning("连续触发高德 QPS 限制，停止继续解析景点")
                    break
            logger.warning("跳过景点 %r：%s", name, e)
            continue

        consecutive_qps_failures = 0
        if place is not None:
            places.append(place)

    if not places and destination.strip():
        # 没有景点坐标时，至少把城市中心画出来。这样结果页不会是一块空白。
        center = search_place(client, key, destination, destination)
        if center is not None:
            places = [center]

    if not places:
        raise AmapUpstreamError("没有解析到可展示的坐标")

    return places


def search_place(
    client: httpx.Client, key: str, name: str, city: str = ""
) -> PlaceMedia | None:
    """按景点名搜 POI，拿坐标、地址和第一张图；失败再退回地理编码。

    顺序不能反：景点名（"大理古城"）用 geocode 常常只能落到行政区中心，
    而 place/text 能直接命中 POI 的准确坐标，还能带 photos。
    """
    name = name.strip()
    if not name:
        return None

    # ---- 1. POI 搜索 ----
    poi = _search_poi(client, key, name, city)
    if poi is not None:
        location = _location_of(poi)
        if location is not None:
            return PlaceMedia(
                name=name,
                longitude=location[0],
                latitude=location[1],
                photo_url=_first_photo_url(poi),
                address=_safe_text(poi.get("address")),
            )

    # ---- 2. 地理编码兜底 ----
    params = {"key": key, "address": name}
    if city.strip():
        params["city"] = city.strip()

    payload = _get_json_with_qps_retry(client, GEOCODE_URL, params)
    _raise_on_amap_error(payload, "geocode/geo")

    geocodes = payload.get("geocodes")
    if isinstance(geocodes, list) and geocodes:
        location = _location_of(geocodes[0])
        if location is not None:
            return PlaceMedia(
                name=name,
                longitude=location[0],
                latitude=location[1],
            )

    return None


def _search_poi(
    client: httpx.Client, key: str, keywords: str, city: str = ""
) -> dict | None:
    """只做一次 POI 搜索，返回第一个结果。"""
    params = {
        "key": key,
        "keywords": keywords,
        "page_size": 1,
        "page_num": 1,
        # photos 是可选字段，不显式要的话高德不返回。
        "show_fields": "photos",
    }
    if city.strip():
        params["region"] = city.strip()
        params["city_limit"] = "true"

    payload = _get_json_with_qps_retry(client, PLACE_URL, params)
    _raise_on_amap_error(payload, "place/text")

    pois = payload.get("pois")
    if isinstance(pois, list) and pois and isinstance(pois[0], dict):
        return pois[0]
    return None


def geocode_address(
    address: str,
    city: str = "",
    *,
    client: httpx.Client | None = None,
    key: str = "",
) -> GeocodePlace | None:
    """把地址解析成坐标，优先地理编码，失败再用 POI 兜底。

    酒店地址和景点名的解析顺序刚好相反：地址用 geocode 更准（能落到门牌/道路），
    POI 搜索适合"某某酒店"这类名称。
    """
    address = address.strip()
    if not address:
        return None

    key = key or require_amap_key()

    if client is None:
        with httpx.Client(
            timeout=settings.AMAP_HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as own_client:
            return _geocode_with_client(own_client, key, address, city)

    return _geocode_with_client(client, key, address, city)


def _geocode_with_client(
    client: httpx.Client, key: str, address: str, city: str
) -> GeocodePlace | None:
    params = {"key": key, "address": address}
    if city.strip():
        params["city"] = city.strip()

    payload = _get_json_with_qps_retry(client, GEOCODE_URL, params)
    _raise_on_amap_error(payload, "geocode/geo")

    geocodes = payload.get("geocodes")
    if (
        isinstance(geocodes, list)
        and geocodes
        and isinstance(geocodes[0], dict)
    ):
        location = _location_of(geocodes[0])
        if location is not None:
            return GeocodePlace(
                longitude=location[0],
                latitude=location[1],
                citycode=_safe_text(geocodes[0].get("citycode")).strip(),
                formatted_address=_safe_text(
                    geocodes[0].get("formatted_address")
                ).strip(),
            )

    # 地理编码没结果时退回 POI，例如"成都春熙路希尔顿酒店"这种名称。
    poi = _search_poi(client, key, address, city)
    if poi is None:
        return None
    location = _location_of(poi)
    if location is None:
        return None
    return GeocodePlace(
        longitude=location[0],
        latitude=location[1],
        citycode=_safe_text(poi.get("citycode")).strip(),
        formatted_address=_safe_text(poi.get("address")).strip(),
    )


def resolve_citycode(
    destination: str,
    *,
    client: httpx.Client | None = None,
    key: str = "",
) -> str:
    """把城市名解析成 transit 接口要求的 citycode（成都 -> 028）。

    geocode 优先，失败再用行政区接口兜底；两者都失败返回空字符串，
    调用方据此跳过公交，不影响步行/骑行/打车。
    """
    city = destination.strip()
    if not city:
        return ""

    key = key or require_amap_key()

    if client is None:
        with httpx.Client(
            timeout=settings.AMAP_HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as own_client:
            return _resolve_citycode_with_client(own_client, key, city)

    return _resolve_citycode_with_client(client, key, city)


def _resolve_citycode_with_client(
    client: httpx.Client, key: str, city: str
) -> str:
    try:
        payload = _get_json_with_qps_retry(
            client, GEOCODE_URL, {"key": key, "address": city}
        )
        _raise_on_amap_error(payload, "geocode/geo")
        geocodes = payload.get("geocodes")
        if (
            isinstance(geocodes, list)
            and geocodes
            and isinstance(geocodes[0], dict)
        ):
            citycode = _safe_text(geocodes[0].get("citycode")).strip()
            if citycode:
                return citycode
    except AmapUpstreamError as e:
        logger.warning(
            "解析城市编码失败（geocode）：%s infocode=%s", e, e.infocode
        )

    try:
        payload = _get_json_with_qps_retry(
            client,
            DISTRICT_URL,
            {"key": key, "keywords": city, "subdistrict": 0},
        )
        _raise_on_amap_error(payload, "config/district")
        districts = payload.get("districts")
        if (
            isinstance(districts, list)
            and districts
            and isinstance(districts[0], dict)
        ):
            return _safe_text(districts[0].get("citycode")).strip()
    except AmapUpstreamError as e:
        logger.warning(
            "解析城市编码失败（district）：%s infocode=%s", e, e.infocode
        )

    return ""


# ---------------------------------------------------------------------------
# 住宿区域检索
# ---------------------------------------------------------------------------

# 模型写的住宿区域是给人看的句子（"春熙路核心商圈（IFS/太古里周边）"），
# 直接当关键词会命中率很差：括号、修饰词都会干扰高德的分词。
_PAREN_RE = re.compile(r"[（(][^）)]*[）)]")
_AREA_SPLIT_RE = re.compile(r"[/／、，,]")
_AREA_NOISE_RE = re.compile(
    r"核心|商圈|边缘|一带|周边|附近|近地铁站|地铁站"
    r"|服务式|中档|高档|经济型|舒适型|亲子|青年旅舍|民宿|公寓|酒店|的"
)
_AREA_SEPARATOR_RE = re.compile(r"[\s/／、，,·•\-—+＋]+")

# 每个区域都搜两档，前端用 tab 切换。顺序也是默认展示顺序。
# 舒适型 = 中档/商务酒店；经济型/民宿 = 经济连锁 + 民宿客栈。
_HOTEL_TIERS = (
    ("comfort", "舒适型", ("舒适型酒店",)),
    ("budget", "经济型/民宿", ("经济型酒店", "民宿")),
)

# 高德的关键词检索会混进相邻档位的 POI（比如搜"民宿"会返回高档酒店），
# 只按关键词分档两档会互相污染，所以再用 keytag 校验一次。
_BUDGET_CATEGORY_WORDS = (
    "经济型",
    "快捷",
    "民宿",
    "客栈",
    "公寓",
    "青年旅舍",
    "青旅",
    "旅馆",
    "招待所",
)


def search_area_hotels(
    destination: str, areas: Iterable[HotelAreaSpec]
) -> tuple[list[HotelAreaResult], list[str]]:
    """按住宿区域检索高德真实酒店。

    返回 (每个区域的检索结果, 全局提示)。单区域失败只写自己的 error，
    不影响其他区域；没有 key 时抛 AmapNotConfigured，由 API 层映射 404。
    """
    key = require_amap_key()

    specs = list(areas)
    warnings: list[str] = []
    if not specs:
        return [], ["行程里没有住宿区域建议"]
    if not destination.strip():
        warnings.append("行程缺少目的地，酒店搜索可能不准确")

    results: list[HotelAreaResult] = []
    # 跨区域去重：模型给的几个区域可能都落在同一条街上（成都三个区域
    # 清洗后都是"春熙路"），不去重的话三张卡片会展示完全相同的酒店。
    seen: set[str] = set()
    with httpx.Client(
        timeout=settings.AMAP_HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
    ) as client:
        for index, spec in enumerate(specs):
            results.append(
                _search_one_area(client, key, destination, index, spec, seen)
            )

    if not any(item.places for item in results):
        warnings.append("高德没有返回可用的酒店信息")
    return results, warnings


def _search_one_area(
    client: httpx.Client,
    key: str,
    destination: str,
    index: int,
    spec: HotelAreaSpec,
    claimed: set[str],
) -> HotelAreaResult:
    """搜一个区域：舒适型和经济型/民宿各搜一档，分别按评分排序。

    `seen` 是本区域的关键词去重；`claimed` 是前面区域已经展示过的酒店，
    只有真正展示出来的酒店才会占用，避免一个区域把候选池全耗尽。
    """
    area_text = _clean_area_text(spec.area, destination)
    seen: set[str] = set()

    tiers: list[HotelTierResult] = []
    for tier_key, label, keywords in _HOTEL_TIERS:
        places, tier_error = _search_hotel_tier(
            client,
            key,
            destination,
            area_text,
            spec.area,
            tier_key,
            keywords,
            seen,
            claimed,
        )
        tiers.append(
            HotelTierResult(
                key=tier_key,
                label=label,
                places=places[:HOTELS_PER_AREA],
                error=tier_error,
            )
        )

    # 小城市或偏远区域可能某一档不足 2 家，用泛化关键词补一次；
    # 补到的结果仍按 keytag 分回两档，不能把经济型混进舒适型。
    if any(len(tier.places) < 2 for tier in tiers):
        extra, extra_error = _fetch_hotel_places(
            client,
            key,
            destination,
            f"{area_text} 酒店".strip(),
            spec.area,
            seen,
            "",
            claimed,
        )
        if extra:
            tiers = [
                HotelTierResult(
                    key=tier.key,
                    label=tier.label,
                    places=_rank_hotel_places(
                        tier.places
                        + [
                            place
                            for place in extra
                            if _hotel_matches_tier(place.category, tier.key)
                        ]
                    )[:HOTELS_PER_AREA],
                    error=tier.error,
                )
                for tier in tiers
            ]
        if extra_error:
            tiers = [
                HotelTierResult(
                    key=tier.key,
                    label=tier.label,
                    places=tier.places,
                    # 只给仍然空着的档位补错误提示；有结果的档位不打扰用户。
                    error=tier.error or (extra_error if not tier.places else ""),
                )
                for tier in tiers
            ]

    # 只把真正展示出来的酒店标记为已占用，后面的区域不会重复推荐。
    for tier in tiers:
        for place in tier.places:
            claimed.add(place.name)

    # 兼容旧前端：合并两档后取评分最高的 3 家。
    merged = _rank_hotel_places(
        [place for tier in tiers for place in tier.places]
    )[:HOTELS_PER_AREA]
    error = ""
    if not merged:
        error = next((tier.error for tier in tiers if tier.error), "")

    return HotelAreaResult(
        index=index,
        area=spec.area,
        level=spec.level,
        places=merged,
        error=error,
        tiers=tiers,
    )


def _search_hotel_tier(
    client: httpx.Client,
    key: str,
    destination: str,
    area_text: str,
    area: str,
    tier_key: str,
    keywords: tuple[str, ...],
    seen: set[str],
    claimed: set[str],
) -> tuple[list[HotelPlace], str]:
    """搜一个档位。经济型/民宿档要搜两个关键词，任一成功就有结果。"""
    places: list[HotelPlace] = []
    errors: list[str] = []

    for word in keywords:
        found, error = _fetch_hotel_places(
            client,
            key,
            destination,
            f"{area_text} {word}".strip(),
            area,
            seen,
            tier_key,
            claimed,
        )
        places.extend(found)
        if error:
            errors.append(error)

    places = _rank_hotel_places(places)
    # 只有所有关键词都失败且没有任何结果时，这一档才算失败。
    error = "" if places else (errors[0] if errors else "")
    return places, error


def _fetch_hotel_places(
    client: httpx.Client,
    key: str,
    destination: str,
    keywords: str,
    area: str,
    seen: set[str],
    tier_key: str = "",
    claimed: set[str] | None = None,
) -> tuple[list[HotelPlace], str]:
    """请求一次高德并把结果收进候选池。tier_key 为空表示先不筛档位。"""
    try:
        pois = _fetch_hotel_pois(client, key, destination, keywords)
    except AmapUpstreamError as e:
        logger.warning(
            "搜索住宿关键词 %r 失败：%s infocode=%s", keywords, e, e.infocode
        )
        return [], str(e)
    return (
        _collect_hotel_places(pois, area, seen, tier_key, claimed),
        "",
    )


def _fetch_hotel_pois(
    client: httpx.Client, key: str, destination: str, keywords: str
) -> list[dict]:
    params = {
        "key": key,
        "keywords": keywords,
        "page_size": HOTEL_SEARCH_PAGE_SIZE,
        "page_num": 1,
        # 评分、商圈、参考消费都在 business 里；图片在 photos 里。
        "show_fields": "business,photos",
    }
    if destination.strip():
        params["region"] = destination.strip()
        params["city_limit"] = "true"

    payload = _get_json_with_qps_retry(client, PLACE_URL, params)
    _raise_on_amap_error(payload, "place/text")

    pois = payload.get("pois")
    if not isinstance(pois, list):
        return []
    return [poi for poi in pois if isinstance(poi, dict)]


def _collect_hotel_places(
    pois: list[dict],
    area: str,
    seen: set[str],
    tier_key: str = "",
    claimed: set[str] | None = None,
) -> list[HotelPlace]:
    """按高德原始顺序收集有坐标的住宿 POI。seen 只在当前区域里去重。"""
    claimed = claimed or set()
    places: list[HotelPlace] = []
    for poi in pois:
        place = _hotel_place_from_poi(poi, area)
        if place is None or place.name in seen or place.name in claimed:
            continue
        # 被筛掉的 POI 不写进 seen：它在另一档里可能仍然有效。
        if tier_key and not _hotel_matches_tier(place.category, tier_key):
            continue
        seen.add(place.name)
        places.append(place)
    return places


def _hotel_matches_tier(category: str, tier_key: str) -> bool:
    """按 keytag 判断酒店属于哪一档；分类缺失时归到舒适型。"""
    is_budget = any(word in category for word in _BUDGET_CATEGORY_WORDS)
    return is_budget if tier_key == "budget" else not is_budget


def _rank_hotel_places(places: list[HotelPlace]) -> list[HotelPlace]:
    """评分高的排前面；没有评分的按高德原始相关度排在后面。

    sorted 是稳定排序，同分酒店仍保持高德返回顺序。
    """
    return sorted(
        places,
        key=lambda place: place.rating if place.rating is not None else -1.0,
        reverse=True,
    )


def _hotel_place_from_poi(poi: dict, area: str) -> HotelPlace | None:
    if not _is_lodging_poi(poi):
        return None
    location = _location_of(poi)
    if location is None:
        return None
    name = _safe_text(poi.get("name")).strip()
    if not name:
        return None

    business = poi.get("business")
    if not isinstance(business, dict):
        business = {}

    category = _safe_text(business.get("keytag")).strip()
    if not category:
        # keytag 缺失时退回 POI 类型的最后一级（宾馆酒店 / 青年旅舍）。
        category = _safe_text(poi.get("type")).split(";")[-1].strip()

    return HotelPlace(
        area=area,
        name=name,
        longitude=location[0],
        latitude=location[1],
        address=_safe_text(poi.get("address")).strip(),
        photo_url=_first_photo_url(poi),
        rating=_rating_of(business),
        category=category,
        business_area=_safe_text(business.get("business_area")).strip(),
        # 高德不提供实时房价，cost 只是 POI 上的参考消费；没有就留空。
        avg_cost=_cost_text(business),
    )


def _is_lodging_poi(poi: dict) -> bool:
    # 高德的住宿 POI 类型都以"住宿服务"开头。关键词搜索偶尔会混进
    # "酒店用品店"之类的非住宿 POI，靠这个字段挡掉。
    return "住宿服务" in _safe_text(poi.get("type"))


def _cost_text(business: dict) -> str:
    """参考人均/消费。0 或非数字按“没有”处理，避免前端显示“参考 ¥0”。"""
    text = _safe_text(business.get("cost")).strip()
    if not text:
        return ""
    try:
        if float(text) <= 0:
            return ""
    except ValueError:
        return text
    return text


def _rating_of(business: dict) -> float | None:
    text = _safe_text(business.get("rating")).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _clean_area_text(area: str, destination: str) -> str:
    """把区域描述压成一个高德能搜的地名。

    例："春熙路核心商圈（IFS/太古里周边）" -> "春熙路"。
    必须先去掉括号再按分隔符切：括号里的 IFS/太古里 有斜杠，
    先切会把括号切成两半，后面的去括号规则就失效了。
    """
    without_parens = _PAREN_RE.sub("", area)
    head = _AREA_SPLIT_RE.split(without_parens, maxsplit=1)[0]
    text = _strip_area_noise(head) or _strip_area_noise(without_parens)
    return text or destination.strip()


def _strip_area_noise(text: str) -> str:
    text = _AREA_NOISE_RE.sub(" ", text)
    text = _AREA_SEPARATOR_RE.sub(" ", text)
    return " ".join(text.split()).strip()


# ---------------------------------------------------------------------------
# 餐厅检索（Phase 2：只运行时查，不落库）
# ---------------------------------------------------------------------------

# 高德的餐饮服务大类。子类很多（中餐厅/火锅/小吃/咖啡…），用大类就能覆盖。
RESTAURANT_AMAP_TYPES = "050000"
RESTAURANT_PAGE_SIZE = 10
RESTAURANT_MAX_LIMIT = 10

# 美食卡片的 area 是模型写的“去哪儿吃”，不是严格地址：
# “市区社区小馆”“这一档”“附近”这类词直接拼进关键词会污染高德的分词。
_RESTAURANT_GENERIC_AREA_WORDS = (
    "市区",
    "社区",
    "小馆",
    "一带",
    "附近",
    "周边",
    "商圈",
    "区域",
    "片区",
    "这一档",
    "老城",
    "市中心",
    "旁",
)

# 模型经常把具体店名写在括号里（“文殊院一带（洞子口张老二凉粉）”）。
# 这种名字比“区域 + 菜名”精确得多，能直接命中那家店。
# 但括号里也可能是地标（“IFS/太古里周边”），所以用店名/食物后缀判断一下。
_RESTAURANT_NAME_HINTS = (
    "店",
    "馆",
    "楼",
    "阁",
    "府",
    "园",
    "记",
    "妈",
    "婆",
    "师",
    "傅",
    "饭店",
    "餐厅",
    "火锅",
    "粉",
    "面",
    "饺",
    "茶",
    "小吃",
    "锅魁",
    "串串",
    "烧烤",
    "酒家",
    "食府",
)
# 括号里常出现“不必挤网红店”“本地人常去”这类建议，虽然带“店”字但不是店名。
_RESTAURANT_NAME_BAD_WORDS = (
    "不必",
    "不要",
    "避开",
    "推荐",
    "任意",
    "这一档",
    "网红",
    "附近",
    "周边",
    "本地人",
    "随便",
)
_PAREN_CONTENT_RE = re.compile(r"[（(]([^）)]*)[）)]")


def search_restaurants(
    destination: str,
    keyword: str,
    area: str = "",
    limit: int = 5,
) -> tuple[list[RestaurantPlace], list[str]]:
    """按“美食名称 + 区域”检索高德真实餐厅。

    只读、不写库；调用方（结果页）在用户点开某张美食卡片时才触发。
    返回 (餐厅列表, 提示)。没有 key 时抛 AmapNotConfigured，由 API 层映射 404。
    """
    key = require_amap_key()

    destination = destination.strip()
    keyword = _clean_restaurant_keyword(keyword)
    if not destination:
        raise ValueError("行程缺少目的地，无法搜索餐厅")
    if not keyword:
        raise ValueError("缺少要搜索的美食名称")
    if not 1 <= limit <= RESTAURANT_MAX_LIMIT:
        raise ValueError(f"limit 只能是 1—{RESTAURANT_MAX_LIMIT}")

    query = _restaurant_query_text(keyword, area)
    with httpx.Client(
        timeout=settings.AMAP_HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
    ) as client:
        places = _fetch_restaurant_places(client, key, destination, query)
        # 精确店名/区域没搜到时退回菜名。多一次高德调用，只在空结果时发生。
        if not places and query != keyword:
            places = _fetch_restaurant_places(client, key, destination, keyword)

    # 高德对无效的 region 会忽略而不是报错（和 search_pois 同一个坑）：
    # 请求一个不存在的城市时，它照样可能返回其他城市的餐厅。
    # 结果页会把这里的餐厅当成“这趟行程附近的店”，所以必须按城市再挡一道。
    matched = [
        place
        for place in places
        if _city_matches(destination, place.cityname)
        or _city_matches(destination, place.adname)
    ]
    if not matched and places:
        logger.warning("高德餐厅返回的城市和请求不一致，已丢弃：requested=%s", destination)
    places = matched

    warnings: list[str] = []
    if not places:
        warnings.append("高德没有返回可用的餐厅信息")
    return places[:limit], warnings


# ---------------------------------------------------------------------------
# 通用 POI 检索（Agent 专家用：景点 / 美食 / 住宿）
# ---------------------------------------------------------------------------


def search_pois(
    keywords: str,
    city: str = "",
    types: str = "",
    limit: int = 8,
) -> list[PoiFact]:
    """按关键词检索高德真实 POI，返回名称/地址/类型/评分/参考人均。

    只读、不写库；给 Agent 专家提供事实依据，不能当报价用。
    没有 key 时抛 `AmapNotConfigured`，业务/上游错误抛 `AmapUpstreamError`。

    ⚠️ 和结果页的 `search_restaurants` 不同：这里不按业务类型二次过滤，
    调用方用 `types` 限定大类。已实测的类型码：
    景点 110000（风景名胜）、餐饮 050000、住宿 100000。
    """
    key = require_amap_key()

    keywords = (keywords or "").strip()
    city = (city or "").strip()
    types = (types or "").strip()
    if not keywords:
        raise ValueError("缺少 POI 关键词")
    if not 1 <= limit <= POI_MAX_LIMIT:
        raise ValueError(f"limit 只能是 1—{POI_MAX_LIMIT}")

    with httpx.Client(
        timeout=settings.AMAP_HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
    ) as client:
        pois = _fetch_pois(client, key, keywords, city, types, limit)
        # 该城市没有这个类型时返回空，退回纯关键词再搜一次。
        # 只在空结果时多一次调用，正常路径不付这笔钱。
        # 类型码本身写错会走高德业务错误，不走这个分支。
        if not pois and types:
            pois = _fetch_pois(client, key, keywords, city, "", limit)

    items = _collect_pois(pois)
    if city:
        # 高德对无效的 region 会【忽略】而不是报错——实测传一个不存在的城市，
        # 它照样返回北京的景点。所以必须用 cityname 再挡一道：
        # 宁可这次没有 POI 事实，也不能把别的城市的地点当成事实喂给模型。
        # 用 cityname 和 adname 两个字段一起匹配：敦煌这类县级市可能
        # cityname=酒泉市、adname=敦煌市；只看 cityname 会把它整城丢掉。
        matched = [
            item
            for item in items
            if _city_matches(city, item.cityname)
            or _city_matches(city, item.adname)
        ]
        if not matched and items:
            logger.warning("高德 POI 返回的城市和请求不一致，已丢弃：requested=%s", city)
        items = matched
    return items[:limit]


_CITY_SUFFIX_RE = re.compile(r"(市|地区|盟|自治州|特别行政区)$")


def _city_matches(city: str, cityname: str) -> bool:
    """判断高德返回的 cityname 是否就是请求的城市。

    只做保守的名字匹配：去掉“市/地区/自治州”等后缀后相等或互相包含。
    匹配不上就丢弃——错城市的事实比没有事实更糟。
    """
    left = _CITY_SUFFIX_RE.sub("", (city or "").strip())
    right = _CITY_SUFFIX_RE.sub("", (cityname or "").strip())
    if not left or not right:
        return False
    return left == right or left in right or right in left


def _fetch_pois(
    client: httpx.Client,
    key: str,
    keywords: str,
    city: str,
    types: str,
    limit: int,
) -> list[dict]:
    params: dict[str, object] = {
        "key": key,
        "keywords": keywords,
        "page_size": limit,
        "page_num": 1,
        # 评分、商圈、参考人均在 business 里；不要 photos，避免响应体和
        # agent_runs.output 无谓变大。
        "show_fields": "business",
    }
    if city:
        params["region"] = city
        params["city_limit"] = "true"
    if types:
        params["types"] = types

    payload = _get_json_with_qps_retry(client, PLACE_URL, params)
    _raise_on_amap_error(payload, "place/text(poi)")
    pois = payload.get("pois")
    return pois if isinstance(pois, list) else []


def _collect_pois(pois: list[dict]) -> list[PoiFact]:
    """把高德 POI 原始条目转成 `PoiFact`，按名称去重。"""
    out: list[PoiFact] = []
    seen: set[str] = set()

    for poi in pois:
        if not isinstance(poi, dict):
            continue
        location = _location_of(poi)
        name = _safe_text(poi.get("name")).strip()
        if location is None or not name or name in seen:
            continue
        seen.add(name)

        business = poi.get("business")
        if not isinstance(business, dict):
            business = {}

        out.append(
            PoiFact(
                name=name,
                address=_safe_text(poi.get("address")).strip(),
                cityname=_safe_text(poi.get("cityname")).strip(),
                adname=_safe_text(poi.get("adname")).strip(),
                type=_safe_text(poi.get("type")).strip(),
                typecode=_safe_text(poi.get("typecode")).strip(),
                longitude=location[0],
                latitude=location[1],
                tel=_safe_text(poi.get("tel")).strip(),
                rating=_rating_of(business),
                business_area=_safe_text(business.get("business_area")).strip(),
                avg_cost=_cost_text(business),
            )
        )
    return out


def _fetch_restaurant_places(
    client: httpx.Client, key: str, destination: str, query: str
) -> list[RestaurantPlace]:
    params = {
        "key": key,
        "keywords": query,
        "types": RESTAURANT_AMAP_TYPES,
        "region": destination,
        "city_limit": "true",
        "page_size": RESTAURANT_PAGE_SIZE,
        "page_num": 1,
        # 评分、商圈、参考人均在 business 里；图片在 photos 里。
        "show_fields": "business,photos",
    }
    payload = _get_json_with_qps_retry(client, PLACE_URL, params)
    _raise_on_amap_error(payload, "place/text(restaurants)")

    pois = payload.get("pois")
    if not isinstance(pois, list):
        return []
    return _rank_restaurants(_collect_restaurants(pois))


def _clean_restaurant_keyword(keyword: str) -> str:
    """把“串串香 / 冒菜”“火锅（牛油锅底）”压成一个高德能搜的词。

    美食名是模型写的展示文案，可能带斜杠、顿号或括号解释。
    取第一个具体菜名即可；取不到再退回原文，避免空关键词。
    """
    text = _PAREN_RE.sub("", keyword).strip()
    head = _AREA_SPLIT_RE.split(text, maxsplit=1)[0].strip()
    return head or text or keyword.strip()


def _restaurant_query_text(keyword: str, area: str) -> str:
    """把“区域 + 美食名”拼成高德能搜的关键词。

    区域是模型写的自然语言（“文殊院一带”“市区社区小馆”），直接拼会污染分词。
    优先级：
      1. 括号里像店名的（“洞子口张老二凉粉”）→ 直接搜店名，最精确
      2. 清洗后仍是具体地名的（“文殊院”“八市”）→ “地名 + 菜名”
      3. 泛称（市区/社区/小馆…）→ 只搜菜名，由 destination 限定城市
    """
    named = _restaurant_name_from_area(area)
    if named:
        return named

    anchor = _clean_area_text(area, "") if area.strip() else ""
    # 菜名里经常重复区域（“文殊院一带小吃” + area“文殊院一带”），
    # 去掉重复后只保留“小吃”，查询会收敛到这一带，而不是全城乱飞。
    keyword_hint = keyword
    if anchor and anchor in keyword_hint:
        keyword_hint = keyword_hint.replace(anchor, " ")
    keyword_hint = keyword_hint.replace("一带", " ").strip() or keyword

    if anchor and not any(word in anchor for word in _RESTAURANT_GENERIC_AREA_WORDS):
        return f"{anchor} {keyword_hint}".strip()
    return keyword_hint


def _restaurant_name_from_area(area: str) -> str:
    """从区域描述的括号里提取一个像店名的候选。

    “文殊院一带（洞子口张老二凉粉）” → “洞子口张老二凉粉”
    “市区社区小馆（明婷饭店、永乐饭店这一档）” → “明婷饭店”
    “春熙路（IFS/太古里周边）” → 空（IFS 是地标，不是店名）
    """
    for content in _PAREN_CONTENT_RE.findall(area):
        candidate = _AREA_SPLIT_RE.split(content, maxsplit=1)[0]
        candidate = _strip_area_noise(candidate)
        if not candidate:
            continue
        if any(word in candidate for word in _RESTAURANT_NAME_BAD_WORDS):
            continue
        if any(word in candidate for word in _RESTAURANT_NAME_HINTS):
            return candidate
    return ""


def _collect_restaurants(pois: list[dict]) -> list[RestaurantPlace]:
    """收集有坐标的餐饮 POI，按名称去重。"""
    places: list[RestaurantPlace] = []
    seen: set[str] = set()
    for poi in pois:
        place = _restaurant_place_from_poi(poi)
        if place is None or place.name in seen:
            continue
        seen.add(place.name)
        places.append(place)
    return places


def _rank_restaurants(places: list[RestaurantPlace]) -> list[RestaurantPlace]:
    """评分高的排前面；没有评分的按高德原始相关度排在后面。"""
    return sorted(
        places,
        key=lambda place: place.rating if place.rating is not None else -1.0,
        reverse=True,
    )


def _restaurant_place_from_poi(poi: dict) -> RestaurantPlace | None:
    if not _is_restaurant_poi(poi):
        return None
    location = _location_of(poi)
    if location is None:
        return None
    name = _safe_text(poi.get("name")).strip()
    if not name:
        return None

    business = poi.get("business")
    if not isinstance(business, dict):
        business = {}

    category = _safe_text(business.get("keytag")).strip()
    if not category:
        # keytag 缺失时退回 POI 类型的最后一级（火锅店 / 小吃快餐店）。
        category = _safe_text(poi.get("type")).split(";")[-1].strip()

    return RestaurantPlace(
        name=name,
        longitude=location[0],
        latitude=location[1],
        address=_safe_text(poi.get("address")).strip(),
        cityname=_safe_text(poi.get("cityname")).strip(),
        adname=_safe_text(poi.get("adname")).strip(),
        photo_url=_first_photo_url(poi),
        rating=_rating_of(business),
        category=category,
        business_area=_safe_text(business.get("business_area")).strip(),
        # 高德餐厅的 cost 是参考人均，缺失时留空，不编价格。
        avg_cost=_cost_text(business),
    )


def _is_restaurant_poi(poi: dict) -> bool:
    # 高德的 type 可能是多段（用 | 分隔）：酒店常常同时挂着
    # “住宿服务;宾馆酒店;五星级宾馆|餐饮服务;中餐厅;中餐厅”。
    # 只看第一段，避免把带餐厅的酒店当成餐厅推荐。
    primary = _safe_text(poi.get("type")).split("|")[0]
    return "餐饮服务" in primary


# ---------------------------------------------------------------------------
# 路线（步行 / 骑行 / 打车 / 公交地铁）
# ---------------------------------------------------------------------------


def fetch_route(
    origin: tuple[float, float],
    destination: tuple[float, float],
    mode: str,
    *,
    client: httpx.Client | None = None,
    key: str = "",
    city1: str = "",
    city2: str = "",
) -> RoutePath | None:
    """取两个坐标之间的一种路线。

    origin/destination 是 (lng, lat)。返回 None 表示高德认为这两点之间
    没有可用路线（跨海、封闭区域等）；网络/业务错误仍抛 AmapUpstreamError。

    transit 的 city1/city2 必须是 citycode（成都 028）；任一个为空直接返回
    None，让调用方跳过公交，不影响其他方式。

    client/key 可以由调用方传入以复用连接；单独调用时自己创建。
    """
    if mode not in ROUTE_MODES:
        raise ValueError(f"不支持的路线方式：{mode}")

    key = key or require_amap_key()

    if mode == "transit":
        city1 = city1.strip()
        city2 = city2.strip()
        if not city1 or not city2:
            return None
        if client is None:
            with httpx.Client(
                timeout=settings.AMAP_HTTP_TIMEOUT_SECONDS,
                follow_redirects=True,
            ) as own_client:
                return _fetch_transit_with_client(
                    own_client, key, origin, destination, city1, city2
                )
        return _fetch_transit_with_client(
            client, key, origin, destination, city1, city2
        )

    endpoint, label = ROUTE_MODES[mode]

    if client is None:
        with httpx.Client(
            timeout=settings.AMAP_HTTP_TIMEOUT_SECONDS,
            follow_redirects=True,
        ) as own_client:
            return _fetch_route_with_client(
                own_client, key, origin, destination, endpoint, label
            )

    return _fetch_route_with_client(client, key, origin, destination, endpoint, label)


def _fetch_route_with_client(
    client: httpx.Client,
    key: str,
    origin: tuple[float, float],
    destination: tuple[float, float],
    endpoint: str,
    label: str,
) -> RoutePath | None:
    params = {
        "key": key,
        "origin": _format_location(origin),
        "destination": _format_location(destination),
        # duration 和 tolls 在 cost 里，折线在 polyline 里；不显式要高德不返回。
        "show_fields": "cost,polyline",
    }

    payload = _get_json_with_qps_retry(
        client, f"{DIRECTION_URL}/{endpoint}", params
    )
    _raise_on_amap_error(payload, f"direction/{endpoint}")

    route = payload.get("route")
    if not isinstance(route, dict):
        return None
    paths = route.get("paths")
    if not isinstance(paths, list) or not paths or not isinstance(paths[0], dict):
        return None

    path = paths[0]
    distance_m = _to_int(path.get("distance"))
    if distance_m is None:
        return None

    duration_s = _to_int(path.get("duration"))
    cost = path.get("cost")
    tolls = 0
    if isinstance(cost, dict):
        if duration_s is None:
            duration_s = _to_int(cost.get("duration"))
        tolls = _to_int(cost.get("tolls")) or 0

    steps = path.get("steps")
    if not isinstance(steps, list):
        steps = []

    polyline_text = ";".join(
        text
        for text in (
            _polyline_text(step.get("polyline"))
            for step in steps
            if isinstance(step, dict)
        )
        if text
    )
    cost_yuan, cost_is_estimate = estimate_route_cost(endpoint, distance_m, tolls)

    return RoutePath(
        mode=endpoint,
        mode_label=label,
        distance_m=distance_m,
        duration_s=duration_s or 0,
        cost_yuan=cost_yuan,
        cost_is_estimate=cost_is_estimate,
        polyline=_parse_polyline(polyline_text),
        summary=_route_summary(steps),
        tolls=tolls,
    )


def _fetch_transit_with_client(
    client: httpx.Client,
    key: str,
    origin: tuple[float, float],
    destination: tuple[float, float],
    city1: str,
    city2: str,
) -> RoutePath | None:
    params = {
        "key": key,
        "origin": _format_location(origin),
        "destination": _format_location(destination),
        "city1": city1,
        "city2": city2,
        "show_fields": "cost,polyline",
    }

    payload = _get_json_with_qps_retry(
        client, f"{DIRECTION_URL}/transit/integrated", params
    )
    _raise_on_amap_error(payload, "direction/transit/integrated")

    route = payload.get("route")
    if not isinstance(route, dict):
        return None
    transits = route.get("transits")
    if not isinstance(transits, list) or not transits:
        return None

    best: dict | None = None
    best_score: float | None = None
    for transit in transits:
        if not isinstance(transit, dict):
            continue
        score = _transit_score(transit)
        if score is None:
            continue
        if best_score is None or score < best_score:
            best = transit
            best_score = score

    if best is None:
        return None
    return _parse_transit_path(best)


def _transit_score(transit: dict) -> float | None:
    """和高德返回顺序无关，统一按"耗时 + 2×票价"挑一条。"""
    cost = transit.get("cost")
    if not isinstance(cost, dict):
        return None
    duration = _to_int(cost.get("duration"))
    if duration is None:
        return None
    fee = _to_float(cost.get("transit_fee"))
    if fee is None:
        fee = 2.0
    return duration + 2.0 * fee


def _parse_transit_path(transit: dict) -> RoutePath | None:
    cost = transit.get("cost")
    if not isinstance(cost, dict):
        cost = {}

    duration_s = _to_int(cost.get("duration"))
    if duration_s is None:
        return None

    fee = _to_float(cost.get("transit_fee"))
    cost_is_estimate = fee is None
    if fee is None:
        # 个别城市不返回票价；按一次公交 2 元估算，并标记为估算。
        fee = 2.0

    distance_m = _to_int(transit.get("distance")) or 0

    segments = transit.get("segments")
    if not isinstance(segments, list):
        segments = []

    polyline_parts: list[str] = []
    line_names: list[str] = []
    has_taxi = False

    for segment in segments:
        if not isinstance(segment, dict):
            continue

        walking = segment.get("walking")
        if isinstance(walking, dict):
            steps = walking.get("steps")
            if isinstance(steps, list):
                for step in steps:
                    if not isinstance(step, dict):
                        continue
                    text = _polyline_text(step.get("polyline"))
                    if text:
                        polyline_parts.append(text)

        bus = segment.get("bus")
        if isinstance(bus, dict):
            for line in bus.get("buslines") or []:
                if not isinstance(line, dict):
                    continue
                text = _polyline_text(line.get("polyline"))
                if text:
                    polyline_parts.append(text)
                label = _safe_text(line.get("name")).strip() or _safe_text(
                    line.get("type")
                ).strip()
                if label and label not in line_names:
                    line_names.append(label)
                # 每段只取第一条线路，其余是同站备选。
                break

        railway = segment.get("railway")
        if isinstance(railway, dict):
            text = _polyline_text(railway.get("polyline"))
            if text:
                polyline_parts.append(text)
            name = _safe_text(railway.get("name")).strip()
            if name and name not in line_names:
                line_names.append(name)

        taxi = segment.get("taxi")
        if isinstance(taxi, dict):
            has_taxi = True
            text = _polyline_text(taxi.get("polyline"))
            if text:
                polyline_parts.append(text)

    summary_parts = line_names[:3]
    if has_taxi:
        summary_parts.append("打车接驳")
    summary = " · ".join(summary_parts) or ("步行接驳" if segments else "")

    return RoutePath(
        mode="transit",
        mode_label=ROUTE_MODES["transit"][1],
        distance_m=distance_m,
        duration_s=duration_s,
        cost_yuan=fee,
        cost_is_estimate=cost_is_estimate,
        polyline=_parse_polyline(";".join(polyline_parts)),
        summary=summary,
    )


def estimate_route_cost(
    mode: str, distance_m: int, tolls: int
) -> tuple[float, bool]:
    """按方式估算费用。只有打车和骑行是估算，步行恒为 0。"""
    if mode == "walking":
        return 0.0, False
    if mode == "bicycling":
        return BICYCLE_COST_YUAN, True
    if mode == "transit":
        return 2.0, True

    km = distance_m / 1000.0
    fare = (
        TAXI_BASE_YUAN
        + TAXI_PER_KM_YUAN * max(0.0, km - TAXI_BASE_KM)
        + tolls
    )
    return round(fare, 1), True


def _route_summary(steps: list) -> str:
    """用前三条不同的道路名做一句摘要；打车接口常常没有 road_name，允许为空。"""
    names: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        name = _safe_text(step.get("road_name")).strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= 3:
            break
    return " · ".join(names)


def _format_location(point: tuple[float, float]) -> str:
    lng, lat = float(point[0]), float(point[1])
    if not (math.isfinite(lng) and math.isfinite(lat)):
        raise ValueError("坐标不是有效数字")
    if not (-180 <= lng <= 180 and -90 <= lat <= 90):
        raise ValueError("坐标超出范围")
    return f"{lng:.6f},{lat:.6f}"


def _polyline_text(value: object) -> str:
    """陆路接口的 polyline 是字符串；公交是 {"polyline": "..."}，两种都兼容。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        inner = value.get("polyline")
        if isinstance(inner, str):
            return inner.strip()
    return ""


def _parse_polyline(
    text: str, max_points: int = MAX_ROUTE_POLYLINE_POINTS
) -> list[list[float]]:
    """把 "lng,lat;lng,lat;..." 解析成去重、降采样后的点列。"""
    if not text or max_points < 2:
        return []

    points: list[list[float]] = []
    for chunk in text.split(";"):
        chunk = chunk.strip()
        if not chunk or "," not in chunk:
            continue
        lng_text, lat_text = chunk.split(",", 1)
        try:
            lng, lat = float(lng_text), float(lat_text)
        except ValueError:
            continue
        if not (math.isfinite(lng) and math.isfinite(lat)):
            continue
        if not (-180 <= lng <= 180 and -90 <= lat <= 90):
            continue
        # 相邻步骤的首尾经常是同一个点，连续去重就够了。
        if points and points[-1][0] == lng and points[-1][1] == lat:
            continue
        points.append([lng, lat])

    if len(points) <= max_points:
        return points

    # 等距抽样并强制保留首尾，避免拐角被抽掉后路线看起来穿墙。
    stride = (len(points) - 1) / (max_points - 1)
    sampled = [points[round(index * stride)] for index in range(max_points)]
    sampled[0] = points[0]
    sampled[-1] = points[-1]
    return sampled


def _to_int(value: object) -> int | None:
    text = _safe_text(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _to_float(value: object) -> float | None:
    text = _safe_text(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _first_photo_url(poi: dict) -> str:
    """取 POI 的第一张图，并尽量升级到 HTTPS。

    高德有些图是 http://store.is.autonavi.com/...，在 HTTPS 页面里会被
    混合内容策略拦掉；实测这个域名支持 HTTPS，直接换协议即可。
    """
    photos = poi.get("photos")
    if not isinstance(photos, list):
        return ""
    for photo in photos:
        if not isinstance(photo, dict):
            continue
        url = photo.get("url")
        if isinstance(url, str) and url.strip():
            url = url.strip()
            if url.startswith("http://"):
                url = "https://" + url[len("http://") :]
            return url
    return ""


def _safe_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "".join(str(item) for item in value if item)
    return str(value)


def _classify_amap_payload(payload: object) -> BreakerOutcome:
    """高德 JSON 的业务结果分类。

    高德的业务错误是 HTTP 200 + status != "1"，不能只靠异常判断。
    - status == "1"：成功
    - infocode == "10021"：QPS 超限，只计限流，不计失败
    - 其他业务错误（key 无效、参数错误等）：忽略，交给调用方现有的
      AmapUpstreamError 映射，不要把它们算成"高德服务挂了"
    """
    if not isinstance(payload, dict):
        return BreakerOutcome.IGNORE
    if str(payload.get("status")) == "1":
        return BreakerOutcome.SUCCESS
    if str(payload.get("infocode") or "") == "10021":
        return BreakerOutcome.THROTTLE
    return BreakerOutcome.IGNORE


def _classify_amap_exception(exc: BaseException) -> BreakerOutcome:
    """区分"高德不可用"和"配置/业务问题"。"""
    if isinstance(exc, AmapNotConfigured):
        # 没配 key 不是上游故障，熔断没有意义。
        return BreakerOutcome.IGNORE
    if isinstance(exc, AmapUpstreamError):
        if exc.infocode == "10021" or exc.http_status == 429:
            return BreakerOutcome.THROTTLE
        # 401/403/404 这类是 key 或参数问题，不是上游挂了。
        # 把它们算成失败会让一个配错的 key 把熔断器打开，
        # 之后所有请求被拒，反而掩盖了真正需要改的配置。
        if exc.http_status is not None and 400 <= exc.http_status < 500:
            return BreakerOutcome.IGNORE
        return BreakerOutcome.FAILURE
    if isinstance(exc, (httpx.HTTPError, TimeoutError, OSError)):
        return BreakerOutcome.FAILURE
    return BreakerOutcome.IGNORE


def _fetch_static_map(
    client: httpx.Client, key: str, points: list[MapPoint]
) -> bytes:
    """取静态地图，外层包 ``amap:static`` 熔断。"""
    try:
        return get_breaker("amap:static").call(
            _fetch_static_map_once,
            client,
            key,
            points,
            classify_exception=_classify_amap_exception,
        )
    except CircuitOpenError:
        # 不把 infocode 设成 10021：render_plan_media 看到 10021 会再试一次，
        # 而熔断中再试必然还是被拒，只会多等一次 QPS_RETRY_DELAY_SECONDS。
        logger.warning("高德静态地图熔断中，跳过本次请求")
        raise AmapUpstreamError("高德静态地图暂时不可用（熔断中）") from None


def _fetch_static_map_once(
    client: httpx.Client, key: str, points: list[MapPoint]
) -> bytes:
    """取静态地图。多个点分成多个样式组，每组带一个字母标签。"""
    markers = "|".join(
        f"large,0x6D82DE,{_LABELS[i % len(_LABELS)]}:"
        f"{point.longitude:.6f},{point.latitude:.6f}"
        for i, point in enumerate(points)
    )
    params = {
        "key": key,
        "size": f"{MAP_WIDTH}*{MAP_HEIGHT}",
        "scale": MAP_SCALE,
        "markers": markers,
        "traffic": 0,
    }

    _wait_for_amap_slot()
    try:
        response = client.get(STATIC_MAP_URL, params=params)
        response.raise_for_status()
    except httpx.HTTPError as e:
        # 只记异常类型和状态码：httpx 的异常字符串里可能带完整 URL（含 key）。
        status = getattr(getattr(e, "response", None), "status_code", None)
        logger.warning("高德静态地图请求失败：%s status=%s", type(e).__name__, status)
        raise AmapUpstreamError(
            "高德静态地图请求失败", http_status=status
        ) from None

    content_type = response.headers.get("content-type", "")
    if "image" not in content_type or not response.content:
        # 高德出错时也可能返回 HTTP 200 + JSON，这种情况下不能把 JSON 当图片下发。
        infocode = ""
        info = ""
        if "application/json" in content_type:
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    infocode = str(payload.get("infocode") or "")
                    info = str(payload.get("info") or "")
            except ValueError:
                pass
        logger.warning(
            "高德静态地图返回了非图片内容：info=%s infocode=%s content-type=%s length=%s",
            info,
            infocode,
            content_type,
            len(response.content),
        )
        raise AmapUpstreamError("高德静态地图没有返回图片", infocode=infocode)

    return response.content


def _get_json_with_qps_retry(client: httpx.Client, url: str, params: dict) -> dict:
    """请求一次；如果只是 QPS 限流，等一会儿再试一次。

    10021 是高德表示"并发/QPS 超限"的 infocode。个人 key 很容易在
    5-6 个景点连续解析时触发它，重试一次比整张地图失败划算。

    整个"首次 + 限流重试"包在 ``amap:json`` 熔断里：
    熔断打开时直接跳过，不再占用 QPS 时隙。
    """

    def _request() -> dict:
        payload = _get_json(client, url, params)
        if str(payload.get("status")) == "1":
            return payload
        if str(payload.get("infocode") or "") != "10021":
            return payload

        time.sleep(QPS_RETRY_DELAY_SECONDS)
        return _get_json(client, url, params)

    try:
        return get_breaker("amap:json").call(
            _request,
            classify_result=_classify_amap_payload,
            classify_exception=_classify_amap_exception,
        )
    except CircuitOpenError:
        logger.warning("高德 JSON 接口熔断中，跳过本次请求")
        raise AmapUpstreamError("高德服务暂时不可用（熔断中）") from None


def _get_json(client: httpx.Client, url: str, params: dict) -> dict:
    # 所有 JSON 接口（POI / 地理编码 / 以后的路线）都从这里出去，
    # 把节流放在这一层，调用方就不需要各自 sleep。
    _wait_for_amap_slot()
    try:
        response = client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        logger.warning("高德接口请求失败：%s status=%s", type(e).__name__, status)
        raise AmapUpstreamError("高德接口请求失败", http_status=status) from None

    if not isinstance(payload, dict):
        raise AmapUpstreamError("高德接口返回了非对象 JSON")
    return payload


def _raise_on_amap_error(payload: dict, endpoint: str) -> None:
    """高德的业务错误是 HTTP 200 + status != "1"，必须显式判断。"""
    if str(payload.get("status")) == "1":
        return

    infocode = str(payload.get("infocode") or "")
    info = str(payload.get("info") or "未知错误")
    logger.warning("高德 %s 返回错误：info=%s infocode=%s", endpoint, info, infocode)
    raise AmapUpstreamError(f"高德接口错误：{info}", infocode=infocode)


def _location_of(item: dict) -> tuple[float, float] | None:
    """把高德的 "lng,lat" 字符串解析成两个 float。"""
    if not isinstance(item, dict):
        return None
    location = item.get("location")
    if not isinstance(location, str) or "," not in location:
        return None
    lng_text, lat_text = location.split(",", 1)
    try:
        return float(lng_text), float(lat_text)
    except ValueError:
        return None


def _dedupe_names(names: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        text = str(name).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out
