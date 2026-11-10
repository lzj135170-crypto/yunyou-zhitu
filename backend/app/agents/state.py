"""LangGraph 旅行智能体的全局状态。

节点返回的 dict 由 LangGraph 合并：配了 reducer 的字段合并，没配的覆盖。
覆盖不会报错，因此 done 和 findings 必须带 reducer，否则专家产出会互相冲掉。
状态最终写入检查点和 agent_runs.output，字段必须可 JSON 序列化。
"""

from operator import add
from typing import Annotated, Any

from langgraph.graph.message import MessagesState


def merge_findings(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """按 key 合并专家产出；必须是纯函数，不能原地修改旧值。

    写入状态的对象必须可 JSON 序列化，Pydantic 模型先 model_dump。
    """
    return {**left, **right}


class TravelState(MessagesState):
    """图运行期间在节点之间传递的状态；messages 由 MessagesState 自带 reducer。"""

    # 本轮用户问题，供 Planner 意图识别。
    user_query: str

    # 本轮 trace ID，用于把检查点和 agent_run 关联起来。
    trace_id: str

    # Planner 产出的任务清单；只写一次，不需要 reducer。
    tasks: list[str]

    # 已完成任务；必须追加，否则 Planner 会重复调度。
    done: Annotated[list[str], add]

    # 各专家产出；必须合并，否则后写的会覆盖先写的。
    findings: Annotated[dict[str, Any], merge_findings]

    # 最终回复；首次写入前不存在，读取用 state.get()。
    final_answer: str

    # 生成后依据校验报告，单写者，不需要 reducer。
    grounding: dict[str, Any]

    # 本轮所有 LLM 调用 token 累加。
    tokens: Annotated[int, add]
