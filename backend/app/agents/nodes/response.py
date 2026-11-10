"""Response Agent：把各专家结论组织成给用户看的自然语言回复。

它是图的终点，所以返回 dict 而不是 Command；同时负责把 AI 回复追加进 messages，
否则下一轮历史里只有用户的问题，没有上一轮的回答。
"""

import json
import logging
from typing import Any

from langchain_core.messages import AIMessage

from app.agents.grounding import guard_answer
from app.agents.llm import invoke_text
from app.agents.nodes._common import recent_history
from app.agents.prompts import RESPONSE_PROMPT
from app.agents.state import TravelState

logger = logging.getLogger(__name__)


def _fallback_answer(state: TravelState) -> str:
    """LLM 不可用时的降级回复：如实列出专家产出，并说明没有正式回复的原因。"""

    findings = state.get("findings") or {}
    if not findings:
        return "【降级回复】智能体链路未启用（未配置可用的大模型 key，或模型服务暂时不可用），且没有可展示的分析结果。"

    lines = ["【降级回复】未配置可用的大模型 key（DEEPSEEK_API_KEY / MIMO_API_KEY），或模型服务暂时不可用，无法生成自然语言回复。以下是各专家的原始产出："]
    for name in sorted(findings):
        lines.append(f"· {name}:{findings[name]}")
    return "\n".join(lines)


def response_node(state: TravelState) -> dict[str, Any]:
    """生成最终回复。"""
    findings = state.get("findings") or {}
    # 传节点名而不是 provider 名，模型分配只由 llm.NODE_PROVIDERS 决定。
    # invoke_text 内部处理熔断和 MiMo 回退；None 表示本轮没有可用模型。
    try:
        # recent_history 最后一条就是本轮用户问题，不用再复述。
        # findings 用 JSON 渲染，避免 Python repr 把嵌套结构挤成一行。
        rendered = json.dumps(findings, ensure_ascii=False, indent=2, default=str)
        text, tokens = invoke_text(
            RESPONSE_PROMPT,
            recent_history(state),
            f"各专家的分析结果：\n{rendered}",
            node="response",
        )
        if text is not None:
            # 生成后校验：校验器自身失败会原样返回，不需要 try/except。
            safe_text, report = guard_answer(text, state)
            return {
                "final_answer": safe_text,
                "done": ["response"],
                # AI 回复也要进历史，下一轮才能理解上下文。
                "messages": [AIMessage(content=safe_text)],
                # 加上校验器改写消耗的 token。
                "tokens": tokens + report.tokens,
                "grounding": report.to_dict(),
            }
    except Exception as e:
        logger.warning("Response Agent 生成失败，改用降级回复：%s", e)

    # 降级路径也必须写 messages 和 done，否则下一轮丢历史和重复调度。
    text = _fallback_answer(state)
    return {
        "final_answer": text,
        "done": ["response"],
        "messages": [AIMessage(content=text)],
        # 降级文案本身是如实罗列 findings，不再跑校验；报告里说明原因。
        "grounding": {
            "checked": False,
            "action": "skipped",
            "reason": "fallback_answer",
        },
    }
