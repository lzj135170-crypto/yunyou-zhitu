"""Transport Agent：交通规划。

同样没有外部数据源。票价按区间给（price_range，用 str 的理由同 attraction），
但时刻表仍然【不给】——班次和时刻比价格变得快得多，
给一个看起来像真的时刻，用户照着赶车是真的会耽误事。
"大致耗时"和"适合什么场景"这类经验性判断不受影响，照旧。
"""

import logging
from typing import Literal

from langgraph.types import Command
from pydantic import BaseModel, Field

from app.agents.nodes._common import run_expert
from app.agents.prompts import TRANSPORT_PROMPT
from app.agents.state import TravelState

logger = logging.getLogger(__name__)


class TransportOption(BaseModel):
    """一种交通方式。"""

    mode: str = Field(description="交通方式，如飞机 / 高铁 / 自驾 / 地铁+打车")
    duration: str = Field(default="", description="大致耗时，只能给量级，如「约 2 小时」")
    best_for: str = Field(default="", description="适合什么场景，如赶时间 / 省钱 / 行李多")
    price_range: str = Field(
        default="",
        description=(
            "票价的【大致区间】，形如「约 500-600 元」。必须说明是估算、"
            "以购票平台为准，不要给精确到个位的数字。没有把握就留空"
        ),
    )
    note: str = Field(default="", description="其他要注意的点")


class TransportPlan(BaseModel):
    """交通方案的产出。

    提示词要求同时覆盖"怎么去"和"到了之后怎么动"，所以 options 是一个
    不区分的平铺列表，靠 mode 和 best_for 自然区分——强行拆成两个字段
    反而会在用户只问了其中一件事时逼模型硬编另一半。
    """

    options: list[TransportOption] = Field(description="3 到 4 个方案，覆盖不同场景")
    trip_note: str = Field(default="", description="关于订票时机的经验性建议")


INSTRUCTION = "请根据上面的对话，给出交通方案。"


def transport_node(state: TravelState) -> Command[Literal["planner"]]:
    """交通规划节点。"""
    return run_expert(
        state,
        name="transport",
        prompt=TRANSPORT_PROMPT,
        schema=TransportPlan,
        instruction=INSTRUCTION,
    )
