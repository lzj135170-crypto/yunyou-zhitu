from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.conversation import (
    ConversationCreateRequest,
    ConversationResponse,
    ConversationUpdateRequest,
)
from app.services import conversation_service

router = APIRouter(prefix="/conversations", tags=["会话"])

# 说明：路由层只做三件事——解析参数、调服务、包统一的 Response。
# 归属校验、事务提交都在 app/services/ 里，这里不做任何业务判断。


# 路径写 "" 而不是 "/"：配合 prefix 得到的正是 /api/conversations，不带尾斜杠。
# 若写 "/"，客户端访问不带斜杠的地址会被 307 重定向，多一次往返，
# POST 还得靠 307 保持方法体和请求体。
@router.post("")
def create_conversation(
    data: ConversationCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ 新建会话（不传 title 则等首条消息自动生成） """
    conversation = conversation_service.create_conversation(
        db, current_user.user_id, data.title
    )
    return Response.success(
        message="创建成功",
        data=ConversationResponse.model_validate(conversation),
    )


@router.get("")
def list_conversations(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ 我的会话列表，最近有动静的排最前 """
    items = conversation_service.list_conversations(
        db, current_user.user_id, limit, offset
    )
    return Response.success(
        message="请求成功",
        data=[ConversationResponse.model_validate(c) for c in items],
    )


@router.get("/{conversation_id}")
def get_conversation(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ 会话详情（前端从深链进来时取标题用） """
    conversation = conversation_service.get_owned_conversation(
        db, conversation_id, current_user.user_id
    )
    return Response.success(
        message="请求成功",
        data=ConversationResponse.model_validate(conversation),
    )


@router.patch("/{conversation_id}")
def update_conversation(
    conversation_id: int,
    data: ConversationUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ 重命名会话（对自动生成的标题不满意时用） """
    conversation = conversation_service.update_conversation_title(
        db, conversation_id, current_user.user_id, data.title
    )
    return Response.success(
        message="修改成功",
        data=ConversationResponse.model_validate(conversation),
    )


@router.delete("/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ 删除会话（软删除：只写 deleted_at，消息和运行记录保留） """
    conversation_service.delete_conversation(db, conversation_id, current_user.user_id)
    return Response.success(message="删除成功")
