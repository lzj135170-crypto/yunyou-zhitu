"""Attraction Agent：景点推荐。

数据来源是模型自己的知识，不是外部接口。票价、开放时间这类会变的事实【可以给】，
但只能给区间并标明是估算——所以 price_range 用 str 而不是数字类型：
一个 float 字段会被下游当成实价去用，一段"约 50-80 元"则自带不确定性。
提示词里也写了同样的要求，两边一致。
"""

import logging
from typing import Literal

from langgraph.types import Command
from pydantic import BaseModel, Field

from app.agents.nodes._common import run_expert
from app.agents.nodes._poi import PoiRequest
from app.agents.prompts import ATTRACTION_PROMPT
from app.agents.state import TravelState

logger = logging.getLogger(__name__)


class Attraction(BaseModel):
    """单个景点。"""

    name: str = Field(description="景点名称")
    reason: str = Field(description="推荐理由，一两句话，要结合用户的偏好而不是泛泛而谈")
    hours: float = Field(description="建议游玩时长，单位小时")
    price_range: str = Field(
        default="",
        description=(
            "门票的【大致区间】，形如「约 50-80 元」。必须带上「约」「起」这类字眼"
            "表明是估算，不要写成精确金额。完全没有把握就留空——"
            "宁可不给，也不要为了填满而编一个数"
        ),
    )


class AttractionList(BaseModel):
    """景点推荐的产出。

    用一层壳包住 list 而不是直接返回 list[Attraction]：
    with_structured_output 要求顶层是对象（JSON Schema 的根必须是 object），
    直接给数组会拿不到结果。
    """

    attractions: list[Attraction] = Field(description="3 到 6 个景点，不要罗列一大堆")


# 结尾再给一句明确的指令，作用是把模型从"读对话"切到"执行这个任务"。
# 历史消息的最后一条就是用户这一轮的问题，模型容易接着往下聊天，
# 而不是产出结构化结果——这句就是防这个的。
INSTRUCTION = "请根据上面的对话，推荐合适的景点。"


def attraction_node(state: TravelState) -> Command[Literal["planner"]]:
    """景点推荐节点。骨架逻辑全在 run_expert 里。"""
    return run_expert(
        state,
        name="attraction",
        prompt=ATTRACTION_PROMPT,
        schema=AttractionList,
        instruction=INSTRUCTION,
        # 110000 = 高德风景名胜大类；名称/地址来自 POI，票价仍按估算。
        poi=PoiRequest(keywords="景点", types="110000"),
    )
