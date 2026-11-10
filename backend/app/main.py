import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import HTTPException, RequestValidationError
from fastapi.staticfiles import StaticFiles
from starlette.responses import PlainTextResponse
from app.models import User  # noqa: F401  导入模型包，触发所有模型注册到 Base.metadata
from app.api import api
from app.common import cache
from app.common.exceptions import (
    BusinessException,
    bussiness_excpetion_hadler,
    http_excpetion_hadler,
    validation_excpetion_hadler,
    global_excpetion_hadler
)
from app.common.tracing import TraceIdMiddleware, configure_logging

logger = logging.getLogger(__name__)

# 不在启动时 create_all；schema 演进统一走 Alembic upgrade head。


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：退出时关闭检查点连接池和 MCP 子进程。

    不在启动时预热 checkpointer：连接失败应降级为无记忆运行，而不是拒绝启动。
    Redis 只探一次；不可用时结果缓存直接走进程内降级，不让首个请求等连接超时。
    """
    cache.probe()
    yield

    from app.agents.checkpointer import close

    close()

    # MCP server 是独立子进程；不关会留孤儿进程/管道。
    from app.services.mcp_weather import close as close_mcp_weather

    close_mcp_weather()


# 尽早配置，让启动阶段之后的 app 日志也带 trace/span 字段。
configure_logging()

app = FastAPI(
    title="YUNYOU ZHITU API",
    description="云游织图：多智能体旅行规划、RAG 出处校验与结果页数据服务。",
    version="1.0.0",
    lifespan=lifespan,
)
# 纯 ASGI 中间件：只给 /api 请求生成 trace_id，并在响应头回传。
app.add_middleware(TraceIdMiddleware)
app.include_router(api)

# 注册自定义异常
app.add_exception_handler(BusinessException, bussiness_excpetion_hadler)
app.add_exception_handler(HTTPException, http_excpetion_hadler)
app.add_exception_handler(RequestValidationError, validation_excpetion_hadler)
# 全局异常
app.add_exception_handler(Exception, global_excpetion_hadler)


# 前端构建产物；必须放在 API 路由之后注册，否则 "/" 会吞掉 /api/* 请求。
FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


class _SafeStaticFiles(StaticFiles):
    """静态文件服务；把 Windows 上路径相关的 OSError 转成 404，避免误报 500。"""

    async def get_response(self, path: str, scope):
        try:
            response = await super().get_response(path, scope)
        except OSError:
            return PlainTextResponse("Not Found", status_code=404)

        # Vite 的资源带内容哈希，可以长期缓存；index.html 不能缓存。
        # Windows 上 StaticFiles 传进来的相对路径用反斜杠，先统一。
        normalized = path.replace("\\", "/")
        if normalized in ("", ".", "index.html") or normalized.endswith(".html"):
            response.headers["Cache-Control"] = "no-cache"
        elif normalized.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


if (FRONTEND_DIR / "index.html").is_file():
    app.mount("/", _SafeStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    logger.warning("前端尚未构建：%s 不存在，/ 将返回构建提示", FRONTEND_DIR)

    @app.get("/", include_in_schema=False)
    def frontend_not_built():
        return PlainTextResponse(
            "前端尚未构建。请在 frontend 目录执行 npm ci && npm run build，然后重启服务。",
            status_code=503,
        )

# "/" 留给前端首页；健康检查可用 /api/conversations（未登录返回 401）。

