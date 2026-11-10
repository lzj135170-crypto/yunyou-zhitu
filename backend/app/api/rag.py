from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.rag import RagSearchRequest
from app.services import rag_service

router = APIRouter(prefix="/rag", tags=["知识库"])

# 说明：和其余四组路由一样，路由层只做三件事——解析参数、调服务、包统一的 Response。
# 归属校验和降级判断都在 app/services/rag_service.py 里。


# 路径写 "/search"，配合 prefix 得到 /api/rag/search。
@router.post("/search")
def search_knowledge(
    data: RagSearchRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """检索知识库，返回原始资料片段及出处，不做生成。

    需要登录：语料是系统积累的数据，检索也会消耗嵌入和重排算力。
    """
    result = rag_service.search(db, data, current_user.user_id)
    return Response.success(message="检索成功", data=result)
