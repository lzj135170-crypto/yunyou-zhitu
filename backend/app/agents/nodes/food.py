"""Food Agent：美食推荐。

数据来源是知识库资料 + 模型对当地饮食文化的理解，不接高德等外部接口。
具体餐厅的名称、评分、地址这类事实【不要编】：高德的 POI 检索是另一条路，
本节点只负责"吃什么、去哪条美食街、大概什么价位"。

价格用 str 而不是数字，理由和景点/住宿一致：一个 float 会被下游当成实价，
一段"约 30-60 元/人"则自带不确定性。
"""

import logging
from typing import Literal

from langgraph.types import Command
from pydantic import BaseModel, Field

from app.agents.nodes._common import run_expert
from app.agents.nodes._poi import PoiRequest
from app.agents.prompts import FOOD_PROMPT
from app.agents.state import TravelState

logger = logging.getLogger(__name__)


class Food(BaseModel):
    """一条美食推荐。"""

    name: str = Field(description="菜品、小吃或美食街名称，如「火锅」「小笼包」「八市」")
    area: str = Field(
        default="",
        description="推荐去哪里吃，写到街区/商圈一级，如「春熙路一带」「八市」；不确定留空",
    )
    category: str = Field(
        default="",
        description="类型：必吃菜/小吃/主食/甜品/夜宵/美食街",
    )
    reason: str = Field(description="推荐理由，结合用户偏好，一两句话")
    price_range: str = Field(
        default="",
        description=(
            "人均的【大致区间】，形如「约 30-60 元/人」。必须带「约」「起」这类字眼，"
            "不要写成精确金额；没有把握就留空"
        ),
    )
    meal_time: str = Field(
        default="",
        description="适合时段：早餐/午餐/晚餐/夜宵/全天",
    )


class FoodList(BaseModel):
    """美食推荐的产出。

    用一层壳包住 list：with_structured_output 要求 JSON Schema 的根是 object。
    """

    foods: list[Food] = Field(description="4 到 8 个推荐，覆盖菜品、小吃和美食街")
    dining_tip: str = Field(
        default="",
        description="一段整体吃法建议：口味、排队、避坑、饮食限制，可为空",
    )


INSTRUCTION = "请根据上面的对话，推荐当地值得吃的美食。"


def food_node(state: TravelState) -> Command[Literal["planner"]]:
    """美食推荐节点。骨架逻辑全在 run_expert 里。"""
    return run_expert(
        state,
        name="food",
        prompt=FOOD_PROMPT,
        schema=FoodList,
        instruction=INSTRUCTION,
        # 050000 = 高德餐饮服务大类；店名来自 POI，人均仍按估算。
        poi=PoiRequest(keywords="美食", types="050000"),
    )
