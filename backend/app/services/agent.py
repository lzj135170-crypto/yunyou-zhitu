"""智能体调用入口：把会话映射为 thread_id、驱动 LangGraph、返回 AgentReply。

本层保持很薄，不含提示词和业务判断；智能体行为在 app/agents/nodes/。
"""

import logging
import time
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agents.checkpointer import thread_id_for
from app.agents.graph import get_travel_graph
from app.common.tracing import current_trace_id, trace_span
from app.models.conversation import Conversation
from app.models.message import Message

logger = logging.getLogger(__name__)

# 正常十几步完成；撞上上限说明图在绕圈，多半是节点漏写 done。
RECURSION_LIMIT = 30


class AgentReply(BaseModel):
    """智能体一次调用的产出，字段分别落到 messages 和 agent_runs。"""

    content: str
    agent_name: str = "travel_agent"
    total_tokens: int = 0
    output: dict[str, Any] | None = None


def generate_reply(
    db: Session,
    conversation: Conversation,
    user_message: Message,
) -> AgentReply:
    """调用智能体图生成本轮回复。

    契约：只产出、不写数据库/不 commit；不修改调用方的 ORM 对象；允许抛异常。
    图内部自行创建只读 Session，因此 db 参数暂时保留但未使用。
    """
    # thread_id 决定多轮记忆归属；只使用主键，避免触发懒加载。
    trace_id = current_trace_id()
    config = {
        "configurable": {"thread_id": thread_id_for(conversation.id)},
        "recursion_limit": RECURSION_LIMIT,
    }
    if trace_id:
        # 供 LangGraph 的日志/回调使用；节点 span 走 contextvar。
        config["metadata"] = {"trace_id": trace_id}

    started = time.perf_counter()
    try:
        with trace_span("graph.travel_agent", "graph"):
            result = get_travel_graph().invoke(
                {
                    "user_query": user_message.content,
                    # 写入状态后，检查点也能关联到本次 agent_run。
                    "trace_id": trace_id,
                },
                config=config,
            )
    except Exception:
        # 补会话 id 再抛；图内部堆栈看不出是哪一轮。
        logger.exception("智能体图执行失败（会话 %s）", conversation.id)
        raise

    elapsed = time.perf_counter() - started
    final = (result.get("final_answer") or "").strip()

    logger.info(
        "会话 %s 一轮完成：%.1fs，任务 %s，token %s",
        conversation.id,
        elapsed,
        result.get("tasks"),
        result.get("tokens", 0),
    )

    if not final:
        # 正常不该发生：response 有降级分支。避免前端空白。
        logger.warning("会话 %s 的图执行完成但没有产出回复", conversation.id)
        final = "智能体没有生成回复，请重试。"

    return AgentReply(
        content=final,
        agent_name="travel_agent",
        total_tokens=int(result.get("tokens") or 0),
        # 记录调度过程、专家产出和校验报告，供排查回答依据。
        output={
            "tasks": result.get("tasks") or [],
            "done": result.get("done") or [],
            "findings": result.get("findings") or {},
            "grounding": result.get("grounding") or {},
            "elapsed_seconds": round(elapsed, 1),
        },
    )
