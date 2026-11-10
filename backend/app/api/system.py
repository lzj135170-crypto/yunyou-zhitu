"""只读的系统状态接口。

目前只有依赖熔断状态。它不查库、不改任何状态，纯内存读取，
所以放在普通路由里，不需要额外的限流或缓存。

需要登录：熔断状态会暴露"哪个上游正在故障"，虽然是运维信息，
但没必要对公网匿名开放。
"""

from fastapi import APIRouter, Depends

from app.common.resilience import snapshot_all
from app.common.response import Response
from app.dependencies.auth import get_current_user
from app.models.user import User

router = APIRouter(prefix="/system", tags=["系统"])


@router.get("/dependencies")
def get_dependencies(current_user: User = Depends(get_current_user)):
    """当前进程内各外部依赖的熔断状态和计数。

    ⚠️ 这是进程级快照。多 worker 部署时每个 worker 各有一份状态，
    请求打到哪个 worker 就看到哪一份；不要拿它当全局共享状态。
    """
    return Response.success(message="请求成功", data=snapshot_all())
