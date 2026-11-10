"""Itinerary Agent：把专家产出汇总成按天行程。

它是固定的收尾第一步，不参与 Planner 任务拆分。
和域专家不同，它的指令需要完整 findings，而不只是对话历史。
"""

import json
import logging
from typing import Any, Literal

from langgraph.types import Command
from pydantic import BaseModel, Field

from app.agents.nodes._common import run_expert
from app.agents.prompts import ITINERARY_PROMPT
from app.agents.state import TravelState

logger = logging.getLogger(__name__)


class ItineraryDay(BaseModel):
    """行程中的一天。"""

    day: int = Field(description="第几天，从 1 开始")
    morning: str = Field(description="上午的安排")
    afternoon: str = Field(description="下午的安排")
    evening: str = Field(description="晚上的安排")


class ItineraryPlan(BaseModel):
    """行程编排的产出。"""

    days: list[ItineraryDay] = Field(description="按天排列的行程，一天一项")
    notes: str = Field(
        default="",
        description="需要提醒用户的事项，比如某类信息没查到、专家结论有冲突",
    )


def _render_findings(findings: dict[str, Any]) -> str:
    """把专家产出渲染成提示词片段。

    用 JSON 而不是 Python repr，保持可读并避免模型漏字段；
    ensure_ascii=False 避免中文被转义。
    """
    return json.dumps(findings, ensure_ascii=False, indent=2, default=str)


def itinerary_node(state: TravelState) -> Command[Literal["planner"]]:
    """行程编排节点。

    保留专家的 error finding，让模型在 notes 里说明信息缺失，
    而不是当作这块不存在。
    """
    findings = state.get("findings") or {}

    instruction = (
        f"用户的问题：{state.get('user_query', '')}\n\n"
        f"各专家的分析结果：\n{_render_findings(findings)}\n\n"
        "请把这些信息整合成一份按天编排的行程。"
    )

    return run_expert(
        state,
        name="itinerary",
        prompt=ITINERARY_PROMPT,
        schema=ItineraryPlan,
        instruction=instruction,
        # findings 已包含 rag.context，关闭默认注入避免重复。
        ground=False,
    )
