"""按 trace_id 查一次运行的节点级链路。

路径挂在 /conversations/{id} 下，和消息、结果页一样先走 get_owned_conversation：
trace_id 虽然是随机串，但查询接口仍必须校验会话归属，不能靠"猜不到"当权限。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.common.response import Response
from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models.agent_run import AgentRun
from app.models.agent_run_span import AgentRunSpan
from app.models.user import User
from app.schemas.trace import TraceResponse, TraceRun, TraceSpan
from app.services.conversation_service import get_owned_conversation

router = APIRouter(
    prefix="/conversations/{conversation_id}/runs",
    tags=["追踪"],
)

# span 的 attributes 是给排查用的白名单。
# 即使 span 生成逻辑以后不小心塞进了 prompt/URL/用户输入，
# 这里也不会原样返回给接口调用方。
_ALLOWED_ATTRIBUTE_KEYS = {
    "provider",
    "model",
    "parsed_ok",
    "output_chars",
    "degraded",
    "error_type",
    "mcp_tool",
    "mcp_latency_ms",
    "mcp_ok",
    "circuit_open",
    "backend",
    "cache_hit",
    "rows",
    "http_status",
    "task_count",
    # 生成后依据校验（app/agents/grounding.py）
    "violations_before",
    "violations_after",
    "action",
    "retried",
}


def _safe_attributes(attributes: dict | None) -> dict | None:
    if not isinstance(attributes, dict):
        return None
    safe: dict = {}
    for key in _ALLOWED_ATTRIBUTE_KEYS:
        if key not in attributes:
            continue
        value = attributes[key]
        if isinstance(value, str):
            safe[key] = value[:200]
        elif isinstance(value, (int, float, bool)) or value is None:
            safe[key] = value
    return safe or None


@router.get("/{trace_id}/spans")
def get_run_spans(
    conversation_id: int,
    trace_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """返回这次运行的摘要 + 扁平 span 列表。

    扁平而不是嵌套：父子关系由 parent_span_id 表达，
    前端和 scripts/show_trace.py 各自组树，后端不需要假定展示形态。
    """
    get_owned_conversation(db, conversation_id, current_user.user_id)

    run = (
        db.query(AgentRun)
        .filter(
            AgentRun.conversation_id == conversation_id,
            AgentRun.user_id == current_user.user_id,
            AgentRun.trace_id == trace_id,
        )
        .first()
    )
    if run is None:
        # 和"会话不存在"一样，不区分"这个 trace 不属于你"和"根本没有"。
        raise BusinessException(message="运行记录不存在", code=404)

    rows = (
        db.query(AgentRunSpan)
        .filter(AgentRunSpan.run_id == run.id)
        .order_by(AgentRunSpan.start_time.asc(), AgentRunSpan.id.asc())
        .all()
    )

    return Response.success(
        message="请求成功",
        data=TraceResponse(
            run=TraceRun(
                run_id=run.id,
                trace_id=run.trace_id,
                status=run.status,
                agent_name=run.agent_name,
                total_tokens=run.total_tokens,
                latency_ms=run.latency,
                create_time=run.create_time,
                finished_at=run.finished_at,
                error_code=run.error_code,
                error_message=run.error_message,
            ),
            spans=[
                TraceSpan(
                    span_id=row.span_id,
                    parent_span_id=row.parent_span_id,
                    name=row.name,
                    span_type=row.span_type,
                    node_name=row.node_name,
                    status=row.status,
                    start_time=row.start_time,
                    end_time=row.end_time,
                    duration_ms=row.duration_ms,
                    tokens=row.tokens,
                    error_code=row.error_code,
                    error_message=row.error_message,
                    attributes=_safe_attributes(row.attributes),
                )
                for row in rows
            ],
        ),
    )
