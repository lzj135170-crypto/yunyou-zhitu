from fastapi import APIRouter
from app.api.amap import legacy_router as amap_legacy_router
from app.api.amap import router as amap_router
from app.api.auth import router as auth_router
from app.api.conversation import router as conversation_router
from app.api.message import router as message_router
from app.api.rag import router as rag_router
from app.api.system import router as system_router
from app.api.trace import router as trace_router
from app.api.trip import router as trip_router
from app.api.user import router as user_router

api = APIRouter(prefix="/api")

api.include_router(auth_router)
api.include_router(user_router)
api.include_router(conversation_router)
api.include_router(message_router)
# 知识库检索。和上面四组一样需要登录，理由见 app/api/rag.py。
api.include_router(rag_router)
# 结果页：结构化行程 + 静态地图。挂在 /conversations/{id} 下，复用会话归属校验。
api.include_router(trip_router)
# 节点级追踪查询。和消息/结果页一样按会话归属校验。
api.include_router(trace_router)
# 只读的依赖熔断状态。/api/system/dependencies 需要登录。
api.include_router(system_router)
# 高德栅格瓦片代理。公开底图，不需要登录；前端地图瓦片走这里，绕开被拦的
# is.autonavi.com 域名。
api.include_router(amap_router)
# 兼容旧路径 /api/amap/tile（浏览器可能还缓存着旧前端）。
api.include_router(amap_legacy_router)
