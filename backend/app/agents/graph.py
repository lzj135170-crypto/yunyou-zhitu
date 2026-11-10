"""旅行智能体图装配：只负责连接节点，不包含业务逻辑。

START → reset → planner → 专家/itinerary → response → END。
路由由节点返回的 Command(goto=...) 决定，Command[Literal[...]] 标注
让 LangGraph 识别合法跳转目标。节点实现都在 app/agents/nodes/。
"""

import logging
import threading
from functools import wraps
from typing import get_args

from langgraph.graph import END, START, StateGraph

from app.common.tracing import trace_span
from app.agents.nodes import (
    attraction,
    food,
    hotel,
    itinerary,
    planner,
    rag,
    response,
    transport,
    weather,
)
from app.agents.state import TravelState

logger = logging.getLogger(__name__)

# 节点名 → 节点函数。顺序就是建图时注册的顺序，和调度顺序无关
# （调度顺序由 planner 决定）。
NODE_FUNCTIONS = (
    ("reset", planner.reset_node),
    ("planner", planner.planner_node),
    ("rag", rag.rag_node),
    ("attraction", attraction.attraction_node),
    ("food", food.food_node),
    ("hotel", hotel.hotel_node),
    ("transport", transport.transport_node),
    ("weather", weather.weather_node),
    ("itinerary", itinerary.itinerary_node),
    ("response", response.response_node),
)


def _check_node_registry() -> None:
    """校验节点名单在实现、NODE_FUNCTIONS、EXPERT_NODES、PlannerTarget
    和 PlanDecision.tasks 之间一致，不一致在启动时直接抛异常。

    漏改 PlanDecision.tasks 时模型无法选中该专家且不报错，肉眼审查不可靠，
    因此用推导出的集合做机器校验。
    """
    registered = {name for name, _ in NODE_FUNCTIONS}
    experts = set(planner.EXPERT_NODES)
    finishers = set(planner.FINAL_NODES)
    # 除了专家和收尾，图里只该有 reset / planner 这两个控制节点
    expected = experts | finishers | {"reset", "planner"}

    # tasks 是 list[Literal[...]]，需要多剥一层。
    routable = set(get_args(planner.PlannerTarget))
    declarable = set(
        get_args(get_args(planner.PlanDecision.model_fields["tasks"].annotation)[0])
    )

    problems: list[str] = []
    if registered != expected:
        problems.append(
            f"graph.py 的 NODE_FUNCTIONS 与 planner 的名单对不上："
            f"注册进图但 planner 不知道的 {sorted(registered - expected)}；"
            f"planner 认识但没注册进图的 {sorted(expected - registered)}"
        )
    if declarable != experts:
        problems.append(
            f"PlanDecision.tasks 与 EXPERT_NODES 对不上："
            f"少 {sorted(experts - declarable)}、多 {sorted(declarable - experts)}"
            f"（少的那个专家永远不会被选中，且不会有任何报错）"
        )
    if routable != experts | finishers:
        problems.append(
            f"PlannerTarget 与 EXPERT_NODES + FINAL_NODES 对不上："
            f"少 {sorted((experts | finishers) - routable)}、"
            f"多 {sorted(routable - (experts | finishers))}"
        )

    if problems:
        raise RuntimeError(
            "节点名单不一致——下面几处必须一起改，改完再启动：\n  - "
            + "\n  - ".join(problems)
        )


# 启动即校验：名单不一致属于代码错误，不降级。
_check_node_registry()

_graph = None
_graph_lock = threading.Lock()


def _node_update(result):
    """取节点返回的 update 字段；兼容 dict 和 Command 两种返回。"""
    if isinstance(result, dict):
        return result
    update = getattr(result, "update", None)
    return update if isinstance(update, dict) else {}


def _tag_node_span(span, name: str, result) -> None:
    """根据节点返回值补 token 和降级标记。

    专家把错误吞进 findings 不会抛异常，必须检查返回值，否则 trace 会误报成功。
    """
    update = _node_update(result)

    tokens = update.get("tokens")
    if isinstance(tokens, int) and tokens > 0:
        span.tokens = tokens

    findings = update.get("findings")
    if isinstance(findings, dict):
        finding = findings.get(name)
        if isinstance(finding, dict) and finding.get("error"):
            span.mark_degraded("节点返回 error，产物可能不完整")

    if name == "response":
        answer = update.get("final_answer")
        if isinstance(answer, str) and answer.startswith("【降级回复】"):
            span.mark_degraded("最终回复使用了降级文案", failed=False)


def _traced_node(name: str, fn):
    """给节点套一层 span；没有 recorder 时 trace_span 是空操作。"""

    @wraps(fn)
    def wrapper(state):
        with trace_span(f"node.{name}", "node", node_name=name) as span:
            result = fn(state)
            if span is not None:
                _tag_node_span(span, name, result)
            return result

    return wrapper


def build_graph(checkpointer=None):
    """装配并编译图；checkpointer=None 表示不带多轮记忆，便于脚本和测试。"""
    builder = StateGraph(TravelState)

    for name, fn in NODE_FUNCTIONS:
        # 所有节点统一在这里套 span，避免每个节点文件各写一遍埋点，
        # 也避免以后新增节点忘了加。节点自己的异常语义不变。
        builder.add_node(name, _traced_node(name, fn))

    # 只有三条显式边：入口 → reset → planner，以及终点。
    # 其余跳转全部由节点返回的 Command(goto=...) 决定。
    builder.add_edge(START, "reset")
    builder.add_edge("reset", "planner")
    builder.add_edge("response", END)

    return builder.compile(checkpointer=checkpointer)


def get_travel_graph():
    """返回编译好的图，首次调用时构建并缓存。

    懒加载避免 import 时连接数据库。编译后的图线程安全，状态由 checkpointer
    按 thread_id 隔离，可被多个请求共用。
    已知局限：首次构建时数据库不可用会缓存无记忆版本，需重启进程恢复。
    """
    global _graph

    if _graph is not None:
        return _graph

    with _graph_lock:
        if _graph is None:
            from app.agents.checkpointer import get_checkpointer

            _graph = build_graph(checkpointer=get_checkpointer())
    return _graph
