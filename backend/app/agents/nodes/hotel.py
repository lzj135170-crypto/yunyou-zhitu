"""Hotel Agent：住宿推荐。

和 attraction 一样，数据来自模型的知识而非外部接口。
房价只给【区间】不给精确值：price_range 用 str 而不是数字，
一个数字字段会被下游当成实价用，一段"约 200-400 元/晚"则自带不确定性。
level 和 price_range 分工不同——level 说档位（经济/中档），
price_range 说钱，别把两者混成一个字段。
"""

import logging
from typing import Literal

from langgraph.types import Command
from pydantic import BaseModel, Field

from app.agents.nodes._common import run_expert
from app.agents.nodes._poi import PoiRequest
from app.agents.prompts import HOTEL_PROMPT
from app.agents.state import TravelState

logger = logging.getLogger(__name__)


class Hotel(BaseModel):
    """一个住宿选项。"""

    area: str = Field(description="住宿区域或酒店类型，例如「春熙路/太古里一带」")
    level: str = Field(description="价位档，如经济/中档/中高档。这里只说档位，金额填 price_range")
    reason: str = Field(description="推荐理由：位置方便在哪、适合什么人群")
    suitable_for: str = Field(default="", description="适合的人群，如亲子/情侣/独自旅行")
    price_range: str = Field(
        default="",
        description=(
            "每晚的【大致价位区间】，形如「约 200-400 元」。必须说明是估算、"
            "淡旺季差别大，不要写成精确房价。没有把握就留空"
        ),
    )


class HotelList(BaseModel):
    """住宿推荐的产出。"""

    hotels: list[Hotel] = Field(description="2 到 4 个选项，覆盖不同价位档")
    budget_assumption: str = Field(
        default="",
        description="用户没提预算时，说明你按什么价位区间给的（提了预算就留空）",
    )


INSTRUCTION = "请根据上面的对话，推荐合适的住宿。"


def hotel_node(state: TravelState) -> Command[Literal["planner"]]:
    """住宿推荐节点。"""
    return run_expert(
        state,
        name="hotel",
        prompt=HOTEL_PROMPT,
        schema=HotelList,
        instruction=INSTRUCTION,
        # 100000 = 高德住宿服务大类；酒店/区域来自 POI，房价仍按估算。
        poi=PoiRequest(keywords="酒店", types="100000"),
    )
