from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.common.response import Response
from app.common.tracing import current_trace_id
from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.message import (
    MessageListData,
    MessageResponse,
    MessageSendData,
    MessageSendRequest,
)
from app.services import message_service

# conversation_id 直接写在路由前缀里——消息是会话的子资源。
#
# 这样设计不只是为了 URL 好看：它让"每个消息请求都必须先校验会话归属"
# 变成路径结构的必然结果，而不是一句需要靠人记住的纪律。
# 因此本项目【不提供】GET /messages/{id} 这类裸消息端点——
# 那种接口必须在查完之后 JOIN 回 conversations 校验归属，而人一定会漏写。
router = APIRouter(
    prefix="/conversations/{conversation_id}/messages",
    tags=["消息"],
)


@router.get("")
def list_messages(
    conversation_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    before_id: int | None = Query(default=None, ge=1),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ 查看会话消息，从旧到新返回一页。

    翻更早的历史：把当前返回的 items 里第一条（最早那条）的 id 当 before_id 再请求一次。
    """
    items, has_more = message_service.list_messages(
        db, conversation_id, current_user.user_id, limit, before_id
    )
    return Response.success(
        message="请求成功",
        data=MessageListData(
            items=[MessageResponse.model_validate(m) for m in items],
            has_more=has_more,
        ),
    )


@router.post("")
def send_message(
    conversation_id: int,
    data: MessageSendRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ 发消息，同步返回用户消息和 AI 回复 """
    user_message, assistant_message = message_service.send_message(
        db, conversation_id, current_user.user_id, data.content
    )
    return Response.success(
        message="发送成功",
        data=MessageSendData(
            user_message=MessageResponse.model_validate(user_message),
            assistant_message=MessageResponse.model_validate(assistant_message),
            # 中间件生成的 trace_id 和 AgentRun.trace_id 是同一个值。
            trace_id=current_trace_id() or None,
        ),
    )
