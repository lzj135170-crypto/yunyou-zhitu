"""结果页的行程数据结构。

这是给前端消费的只读投影，不是图内部状态：只暴露页面需要的字段，
过滤 RAG 上下文和诊断信息，并在这一层统一做缺失字段归一化。
"""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


class TripDay(BaseModel):
    """整合后的某一天。"""

    day: int = Field(description="第几天，从 1 开始")
    morning: str = ""
    afternoon: str = ""
    evening: str = ""


class TripAttraction(BaseModel):
    """景点推荐。"""

    name: str
    reason: str = ""
    hours: float | None = Field(default=None, description="建议游玩时长（小时），专家估算")
    price_range: str = Field(default="", description="门票大致区间，专家估算")


class TripFood(BaseModel):
    """美食推荐。是菜品/小吃/美食街，不是实时餐厅报价。"""

    name: str
    area: str = Field(default="", description="推荐的街区/商圈，不是具体餐厅地址")
    category: str = Field(default="", description="必吃菜/小吃/主食/甜品/夜宵/美食街")
    reason: str = ""
    price_range: str = Field(default="", description="人均大致区间，专家估算")
    meal_time: str = Field(default="", description="早餐/午餐/晚餐/夜宵/全天")


class TripHotel(BaseModel):
    """住宿建议。"""

    area: str
    level: str = ""
    reason: str = ""
    suitable_for: str = ""
    price_range: str = Field(default="", description="每晚大致区间，专家估算，不是实时房价")


class TripTransport(BaseModel):
    """交通方案。"""

    mode: str
    duration: str = ""
    best_for: str = ""
    price_range: str = Field(default="", description="票价大致区间，专家估算")
    note: str = ""


class TripWeatherDay(BaseModel):
    """一天的预报。数据来自 Open-Meteo，是真实预报。"""

    date: str = ""
    weather: str = ""
    temp_max: float | None = None
    temp_min: float | None = None
    precip_prob: float | None = None


class TripWeather(BaseModel):
    """天气卡片的数据。"""

    city: str = ""
    days: list[TripWeatherDay] = Field(default_factory=list)
    summary: str = ""
    advice: str = ""
    source: str = ""
    error: str = Field(default="", description="天气没查到的原因；为空表示正常")


class TripSource(BaseModel):
    """知识库出处。前端只展示标题，不展示本地文件路径。"""

    title: str
    source: str | None = None
    section: list[str] = Field(default_factory=list)


class TripPhoto(BaseModel):
    """景点图片。URL 来自高德 POI，不含 key；前端直接展示。"""

    name: str
    url: str
    address: str = ""


class TripPlace(BaseModel):
    """可交互地图上的一个点位。坐标是 GCJ-02，和 JS 地图一致。"""

    name: str
    longitude: float
    latitude: float
    address: str = ""
    photo_url: str = ""


class TripHotelPlace(BaseModel):
    """高德检索到的一家真实酒店。

    ⚠️ 没有实时房价和房态：`avg_cost` 是 POI 上的参考消费，缺失时为空，
    前端必须当成"参考信息"展示，不能渲染成报价。
    """

    name: str
    longitude: float
    latitude: float
    address: str = ""
    photo_url: str = ""
    rating: float | None = Field(default=None, description="高德 POI 评分，不是点评网站分")
    category: str = Field(default="", description="高德住宿类型，如舒适型/民宿")
    business_area: str = Field(default="", description="高德标注的所在商圈")
    avg_cost: str = Field(default="", description="参考人均消费，可能是空字符串")


class TripHotelTier(BaseModel):
    """住宿区域里的一个档位：舒适型 / 经济型·民宿。"""

    key: str = Field(description="comfort 或 budget")
    label: str = Field(description="前端展示的档位名称")
    places: list[TripHotelPlace] = Field(default_factory=list)
    error: str = Field(default="", description="这一档检索失败的原因；为空表示正常")


class TripHotelArea(BaseModel):
    """一个模型建议的住宿区域 + 该区域检索到的真实酒店。"""

    index: int = Field(description="对应 plan.hotels 里的下标，前端据此分组")
    area: str = Field(description="模型给出的区域原文")
    level: str = ""
    places: list[TripHotelPlace] = Field(
        default_factory=list, description="两档合并后的酒店，兼容旧前端"
    )
    tiers: list[TripHotelTier] = Field(
        default_factory=list, description="按舒适型 / 经济型·民宿分档"
    )
    error: str = Field(default="", description="这个区域检索失败的原因；为空表示正常")


class TripHotelsResponse(BaseModel):
    """住宿卡片的真实酒店数据。独立于 /media，避免拖慢地图和景点图。"""

    destination: str = ""
    areas: list[TripHotelArea] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TripRestaurantPlace(BaseModel):
    """高德检索到的一家真实餐厅。

    ⚠️ 没有实时排队、订座和菜单价格：`avg_cost` 是 POI 上的参考人均，
    缺失时为空，前端必须当成参考信息展示。
    """

    name: str
    longitude: float
    latitude: float
    address: str = ""
    photo_url: str = ""
    rating: float | None = Field(default=None, description="高德 POI 评分，不是点评网站分")
    category: str = Field(default="", description="高德餐饮类型，如火锅/小吃快餐")
    business_area: str = Field(default="", description="高德标注的所在商圈")
    avg_cost: str = Field(default="", description="参考人均，可能是空字符串")


class TripRestaurantsResponse(BaseModel):
    """某条美食推荐附近的真实餐厅。运行时查询，不写入知识库。"""

    destination: str = ""
    keyword: str = ""
    area: str = ""
    places: list[TripRestaurantPlace] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TripMediaResponse(BaseModel):
    """结果页的地图 + 景点图，一次请求返回，避免重复消耗高德 QPS。"""

    map_png: str = Field(description="静态地图 PNG 的 base64，不含 data: 前缀")
    places: list[TripPlace] = Field(
        default_factory=list, description="点位坐标，供前端 JS 地图渲染"
    )
    photos: list[TripPhoto] = Field(default_factory=list)


class TripPlanResponse(BaseModel):
    """结果页的完整数据。"""

    conversation_id: int
    title: str = ""
    destination: str = ""
    overview: str = Field(default="", description="结果页顶部的一句话概述")
    answer: str = Field(default="", description="智能体最终回复全文，默认折叠展示")

    # 规划表单的镜像。前端发消息时会把表单展开成一段固定格式的提示词，
    # 后端从这段文字里解析回来。这样结果页的预算/日期/人数不依赖浏览器
    # localStorage——换设备打开历史行程也能显示。
    start_date: str = ""
    end_date: str = ""
    travelers: int | None = None
    budget: int | None = None
    pace: str = ""
    hotel_level: str = ""

    days: list[TripDay] = Field(default_factory=list)
    attractions: list[TripAttraction] = Field(default_factory=list)
    foods: list[TripFood] = Field(default_factory=list)
    food_note: str = Field(default="", description="整体吃法建议，来自美食专家")
    hotels: list[TripHotel] = Field(default_factory=list)
    transport: list[TripTransport] = Field(default_factory=list)
    weather: TripWeather | None = None

    budget_assumption: str = ""
    tips: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    sources: list[TripSource] = Field(default_factory=list)

    generated_at: datetime | None = None


# ---------------------------------------------------------------------------
# 按天路线
# ---------------------------------------------------------------------------


class TripRoutePlaceInput(BaseModel):
    """路线请求里的一个点位。

    坐标可以省略：服务层会用景点名去高德补。经纬度必须同时有或同时没有，
    只传一个会让路线算到 (0, 0)。
    """

    name: str = Field(min_length=1, max_length=100)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    latitude: float | None = Field(default=None, ge=-90, le=90)

    @model_validator(mode="after")
    def _require_both_coordinates(self):
        if (self.longitude is None) != (self.latitude is None):
            raise ValueError("经纬度必须同时提供或同时不提供")
        return self


class TripRoutesRequest(BaseModel):
    """路线请求：景点顺序 + 可选的酒店起终点。

    酒店可以只给地址（服务层地理编码），也可以直接给坐标（用户点了推荐酒店）。
    经纬度必须成对出现。
    """

    places: list[TripRoutePlaceInput] = Field(default_factory=list, max_length=12)
    hotel_name: str = Field(default="", max_length=100)
    hotel_address: str = Field(default="", max_length=200)
    hotel_longitude: float | None = Field(default=None, ge=-180, le=180)
    hotel_latitude: float | None = Field(default=None, ge=-90, le=90)

    @model_validator(mode="after")
    def _require_both_hotel_coordinates(self):
        if (self.hotel_longitude is None) != (self.hotel_latitude is None):
            raise ValueError("酒店经纬度必须同时提供或同时不提供")
        return self


class TripRouteOption(BaseModel):
    """一种交通方式的路线结果。"""

    mode: str
    mode_label: str
    distance_m: int
    duration_s: int
    cost_yuan: float = 0.0
    cost_is_estimate: bool = False
    polyline: list[list[float]] = Field(
        default_factory=list, description="[[lng, lat], ...]，GCJ-02"
    )
    summary: str = ""
    estimated: bool = Field(default=False, description="true 表示按直线兜底估算")
    tolls: int = 0


class TripRouteLeg(BaseModel):
    """相邻两个景点之间的一条腿。"""

    from_name: str
    to_name: str
    from_longitude: float
    from_latitude: float
    to_longitude: float
    to_latitude: float
    recommended: TripRouteOption
    alternatives: list[TripRouteOption] = Field(default_factory=list)


class TripDayRoute(BaseModel):
    """一天的路线。places 只列景点，酒店只出现在首尾腿里。"""

    day: int = 1
    places: list[str] = Field(default_factory=list)
    legs: list[TripRouteLeg] = Field(default_factory=list)
    total_distance_m: int = 0
    total_duration_s: int = 0
    total_cost_yuan: float = 0.0
    cost_is_estimate: bool = Field(
        default=False, description="总费用里是否含估算项（打车/骑行）"
    )
    warnings: list[str] = Field(default_factory=list)


class TripRouteHotel(BaseModel):
    """服务层解析后的酒店起终点。citycode 供后续公交路线使用。"""

    name: str = ""
    address: str = ""
    longitude: float
    latitude: float
    citycode: str = ""


class TripRoutesResponse(BaseModel):
    days: list[TripDayRoute] = Field(default_factory=list)
    hotel: TripRouteHotel | None = None
    warnings: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.now)
