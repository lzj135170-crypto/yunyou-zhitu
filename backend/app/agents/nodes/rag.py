"""RAG Agent：从知识库检索旅游资料。

本节点只负责开 Session、调用 retriever、整理 findings，检索逻辑和降级在检索层。
context 带编号和出处供下游使用；backend/note 等诊断字段必须来自本次实际执行，
用于区分语义检索和降级路径。
"""

import logging
from typing import Literal

from langgraph.types import Command

from app.agents.nodes._common import finish_expert, recent_history
from app.agents.state import TravelState
from app.rag.types import to_finding_chunk

logger = logging.getLogger(__name__)

# 取几条由 policy 统一定义；与候选池、上下文预算是一笔账。
from app.rag.policy import TOP_K  # noqa: E402


def rag_node(state: TravelState) -> Command[Literal["planner"]]:
    """从知识库检索资料。

    自行创建只读 Session，保证图能在 FastAPI 之外独立运行。
    检索失败只记录 findings.error，不影响其他专家。
    """
    from app.agents import retriever
    from app.database import SessionLocal

    try:
        db = SessionLocal()
        try:
            found = retriever.search_full(
                db,
                state.get("user_query", ""),
                # 必须带历史，否则"那住宿呢"无法解析目的地，会跨城市召回。
                history=recent_history(state),
                top_k=TOP_K,
            )
        finally:
            db.close()
    except Exception as e:
        # search_full 已处理检索异常，这里只兜底 Session 创建等意外。
        logger.warning("知识库检索失败：%s", e)
        return finish_expert("rag", {"error": f"检索失败：{e}"})

    finding = {
        "hits": found.hits,
        # 不够格当资料时清空 chunks：它会被 response 原样放进提示词。
        # hits 和 branches 保留，用于区分"检索到但不相关"和"没检索到"。
        "chunks": [to_finding_chunk(h) for h in found.retrieved] if found.has_material else [],
        "has_material": found.has_material,
        # context 和 citations 必须成对使用，否则【编号】无法回溯。
        "context": found.context,
        "citations": [
            {
                "index": cite.index,
                "document_id": cite.document_id,
                "title": cite.title,
                "source": cite.source,
                "section": list(cite.section),
            }
            for cite in found.citations
        ],
        # 诊断字段来自本次实际执行，不是能力探测。
        "backend": found.backend,
        "rewritten": found.rewritten,
        "filters": found.filters,
        "timings_ms": found.timings_ms,
        "note": found.note,
        # 各路完整召回列表，只用于排查，不参与决策。
        "branches": found.branches,
    }
    return finish_expert("rag", finding)
