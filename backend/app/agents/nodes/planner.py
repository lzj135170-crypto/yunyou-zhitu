"""Planner Agent（调度中枢）+ 每轮开头的 reset 节点。

这个文件是整个多智能体系统里唯一做「决策」的地方，其余节点都只做「执行」。
把决策收在一处的好处是：想调调度策略，只需要读这一个文件。
"""

import logging
from typing import Any, Literal

from langchain_core.messages import HumanMessage
from langgraph.types import Command, Overwrite
from pydantic import BaseModel, Field

from app.agents.llm import invoke_structured
from app.agents.nodes._common import recent_history
from app.agents.prompts import PLANNER_PROMPT
from app.agents.state import TravelState

logger = logging.getLogger(__name__)

# Planner 只允许派发给这几个专家。增删或改名时必须同步 PlannerTarget、
# PlanDecision.tasks 和 graph.NODE_FUNCTIONS，graph 引入时会校验四处名单。
EXPERT_NODES = ("rag", "attraction", "food", "hotel", "transport", "weather")

# 固定收尾：itinerary 汇总专家产出，response 转成自然语言。
FINAL_NODES = ("itinerary", "response")

# Planner 可以路由到的全部目标
PlannerTarget = Literal[
    "rag",
    "attraction",
    "food",
    "hotel",
    "transport",
    "weather",
    "itinerary",
    "response",
]


class PlanDecision(BaseModel):
    """Planner 的结构化输出。

    tasks 用 Literal 约束，模型只能从合法节点名里挑，避免编出不存在的专家。
    """

    tasks: list[
        Literal["rag", "attraction", "food", "hotel", "transport", "weather"]
    ] = Field(
        default_factory=list,
        description="这一轮需要哪些专家参与，按建议的执行顺序排列。不需要的不要放进来。",
    )
    reason: str = Field(default="", description="为什么这样拆分，一句话说明")


def reset_node(state: TravelState) -> dict[str, Any]:
    """每轮对话开始时清掉上一轮的调度痕迹，必须是图的第一个节点。

    checkpointer 会跨轮保留 tasks / done / findings；不清的话，第二轮会
    直接跳过所有专家，用户收到与问题无关的回复且没有报错。
    done / findings / tokens 配了 reducer，必须用 Overwrite 绕过合并逻辑。
    messages 不清，它是多轮记忆本体；user_query 来自本次输入，本来就是新值。
    """
    return {
        "tasks": [],
        "done": Overwrite([]),
        "findings": Overwrite({}),
        "tokens": Overwrite(0),
        "final_answer": "",
        # messages 用 add_messages reducer，只增不清，承载多轮记忆。
        "messages": [HumanMessage(content=state.get("user_query", ""))],
    }


def _pick_next_task(tasks: list[str], done: list[str]) -> str:
    """挑出下一个未完成任务；全部完成后进入 itinerary → response。

    纯函数、不调模型：图是否绕圈只取决于 done 有没有被正确追加。
    """
    for task in tasks:
        if task not in done:
            return task
    # 专家都跑完了 → itinerary 汇总 → response 生成
    if "itinerary" not in done:
        return "itinerary"
    return "response"


# 美食卡片是产品要求，不能完全交给 LLM。用户明确在问吃，
# 或 planner 已选景点/住宿/交通时，确定性地补上 food。
_FOOD_HINTS = ("吃", "美食", "小吃", "餐厅", "火锅", "烧烤", "夜宵", "早餐", "菜")


def _ensure_food(tasks: list[str], query: str) -> list[str]:
    if "food" in tasks:
        return tasks
    food_intent = any(word in query for word in _FOOD_HINTS)
    trip_intent = any(task in tasks for task in ("attraction", "hotel", "transport"))
    if not (food_intent or trip_intent):
        return tasks
    return [*tasks, "food"]


def _plan_with_llm(state: TravelState) -> tuple[list[str], int]:
    """让 LLM 把用户问题拆成任务清单，返回 (任务清单, token 用量)。

    带上最近历史，才能理解"那酒店呢"这类省略主语的追问。
    失败或未配置 key 时返回空清单，由调用方兜底；Planner 是图的第一站，
    异常必须在这里接住，否则整轮对话会直接 500。
    """
    try:
        decision, tokens = invoke_structured(
            PLANNER_PROMPT,
            recent_history(state),
            instruction="",
            schema=PlanDecision,
            node="planner",
        )
    except Exception as e:
        logger.warning("Planner 调用失败，改用兜底计划：%s", e)
        return [], 0
    if decision is None:
        return [], tokens

    # 再过滤一遍：结构上 Literal 已经保证了取值合法，
    # 但 LLM 可能返回重复项，重复会让同一个专家被调度两次。
    seen: list[str] = []
    for t in decision.tasks:
        if t in EXPERT_NODES and t not in seen:
            seen.append(t)
    return _ensure_food(seen, state.get("user_query", "")), tokens


def _retrieval_first(tasks: list[str]) -> list[str]:
    """把 rag 固定排在任务清单第一位。

    端到端评估发现 25% 的住宿/交通/景点请求没有调用 rag，专家只能凭记忆编价格。
    执行顺序就是 tasks 顺序，rag 排在后面等于没加：专家读 findings["rag"] 时
    检索还没发生。代价是每轮都多一次 Query 改写和精排，这是有意接受的。
    """
    return ["rag"] + [t for t in tasks if t != "rag"]


def planner_node(state: TravelState) -> Command[PlannerTarget]:
    """意图识别 + 任务分发。

    第一次进入时调用 LLM 产出任务清单；之后不再调模型，只按顺序挑
    第一个未完成任务。LLM 只规划一次，避免每步都问模型导致绕圈和额外开销。
    """
    tasks = state.get("tasks") or []

    if not tasks:
        planned, tokens = _plan_with_llm(state)
        if not planned:
            # 兜底：LLM 不可用（没配 key）或者规划报错时，跑一遍全部专家。
            # 这样图至少能完整走完一遍，方便验证拓扑；
            # 而且这种时候专家节点本身也调不了模型，不会产生额外开销。
            planned = list(EXPERT_NODES)
            logger.info("使用兜底计划：%s", planned)

        planned = _retrieval_first(planned)

        update: dict[str, Any] = {"tasks": planned}
        if tokens:
            update["tokens"] = tokens
        return Command(update=update, goto=_pick_next_task(planned, []))

    return Command(goto=_pick_next_task(tasks, state.get("done") or []))
