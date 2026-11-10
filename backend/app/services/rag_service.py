"""知识库检索的业务逻辑。

路由层只解析参数和包装响应；归属校验与降级判断在这一层。
检索只读，不写库、不 commit，也不记录 agent_runs——
避免调试查询污染图中的真实运行统计。
"""

import logging

from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.models.message import Message
from app.rag import pipeline
from app.rag.policy import PIPELINE_ERROR
from app.rag.types import to_api_hit
from app.schemas.rag import RagCitation, RagHit, RagSearchRequest, RagSearchResponse
from app.services.conversation_service import get_owned_conversation

logger = logging.getLogger(__name__)

# 历史窗口由 policy 统一定义，接口和图必须一致。
from app.rag.policy import HISTORY_WINDOW  # noqa: E402


def search(db: Session, request: RagSearchRequest, user_id: int) -> RagSearchResponse:
    """执行一次知识库检索。

    conversation_id 必须校验归属，否则可借改写结果越权推断他人对话。
    """
    history: list[tuple[str, str]] = []
    if request.conversation_id is not None:
        # 不存在 / 不属于当前用户，都会在这里抛 404。
        # 两者的响应完全一致，不泄露"这个 id 存在但归别人"。
        get_owned_conversation(db, request.conversation_id, user_id)
        history = _recent_history(db, request.conversation_id)

    outcome = pipeline.search(
        request.query,
        filters=request.filters,
        top_k=request.top_k,
        history=history,
        rewrite_query=request.rewrite,
        with_context=request.with_context,
    )

    if outcome is None:
        # 接口不降级：降级结果看起来像真实检索，但正文只是文档标题。
        # 图里的 retriever 才降级；两处语义必须一起维护。
        raise BusinessException(
            message="检索服务不可用：向量库连不上。"
            "请确认容器已启动（docker compose -f deploy/pgvector/docker-compose.yml up -d）",
            code=503,
        )

    if outcome.backend == PIPELINE_ERROR:
        # 向量库正常但流水线内部出错；与"容器未启动"区分文案，堆栈见日志。
        raise BusinessException(
            message="检索服务异常：混合检索流水线内部出错，"
            "本次没有可返回的结果。具体原因见后端日志。",
            code=503,
        )

    return RagSearchResponse(
        query=outcome.query,
        rewritten=outcome.rewritten,
        filters=outcome.filters,
        # 投影由 types.to_api_hit 定义。
        # hits 保留用于排查，context 不足格时清空，避免被当成资料。
        hits=[RagHit(**to_api_hit(h)) for h in outcome.candidates],
        has_material=outcome.has_material,
        context=(
            outcome.assembled.context
            if request.with_context and outcome.has_material
            else None
        ),
        citations=[
            RagCitation(
                index=c.index,
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                title=c.title,
                source=c.source,
                section=list(c.section),
            )
            for c in (outcome.assembled.citations if outcome.has_material else [])
        ],
        backend=outcome.backend,
        timings_ms=outcome.timings_ms,
    )


def _recent_history(db: Session, conversation_id: int) -> list[tuple[str, str]]:
    """取最近 N 条 user/assistant 消息，按时间正序转为 human/ai。"""
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.create_time.desc(), Message.id.desc())
        .limit(HISTORY_WINDOW)
        .all()
    )
    # 上面按时间倒序取"最近 N 条"，这里再翻回正序——模型读对话要按发生顺序
    rows.reverse()
    return [
        ("human" if m.role == "user" else "ai", m.content)
        for m in rows
        # system / tool 消息不进历史：它们不是对话内容，
        # 混进去会让模型以为用户说过这些话
        if m.role in ("user", "assistant")
    ]
