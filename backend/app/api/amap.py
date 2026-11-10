"""高德栅格瓦片代理。

部分网络会拦截 webrd*.is.autonavi.com，导致地图底图空白；
浏览器改为请求后端，由后端转发瓦片。
瓦片是公开底图，不含 key 或用户数据，因此不要求登录。
"""

import base64
import logging
import time

import httpx
from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse
from fastapi.responses import Response as FileResponse

from app.common.resilience import (
    BreakerOutcome,
    CircuitOpenError,
    get_breaker,
)

logger = logging.getLogger(__name__)

# 路径刻意不带 "amap"/"tile" 字样：部分广告拦截规则会按关键字屏蔽
# `/amap/`、`/tile/` 这类 URL，连本地同源接口也会被误伤。
router = APIRouter(prefix="/map", tags=["地图"])

# 高德栅格瓦片有 4 个等价域名，轮流用，避免把单个域名打满。
TILE_HOSTS = (
    "webrd01.is.autonavi.com",
    "webrd02.is.autonavi.com",
    "webrd03.is.autonavi.com",
    "webrd04.is.autonavi.com",
)

# 瓦片请求要快：地图平移/缩放会一次发很多个，单个卡住会拖慢整张地图。
TILE_TIMEOUT_SECONDS = 5.0

# 上游失败时返回一张 1x1 透明 PNG，而不是 JSON 错误。
# 瓦片层只认图片；返回 JSON 会让浏览器报图片解码错误，还会让整张地图
# 出现一片红色破图。透明图只是少一块底图，其它瓦片照常显示。
_TRANSPARENT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


legacy_router = APIRouter(prefix="/amap", tags=["地图"], include_in_schema=False)

_TILE_HINT = (
    "高德瓦片代理正常。请带上 x、y、z 参数，"
    "例如 /api/map/raster?x=26&y=11&z=5"
)


@router.get("/raster")
def get_raster_tile(
    request: Request,
    x: str | None = Query(default=None),
    y: str | None = Query(default=None),
    z: str | None = Query(default=None),
):
    """代理一张高德栅格瓦片。坐标由前端 AMap.TileLayer 传入。

    直接用浏览器打开、不带参数时返回说明文本；瓦片层漏带参数时返回透明瓦片。
    两种情况都不返回 422——瓦片层把 422 当成坏图，会在地图上留下破图。
    """
    return _handle_tile(request, x, y, z)


@legacy_router.get("/tile")
def get_legacy_tile(
    request: Request,
    x: str | None = Query(default=None),
    y: str | None = Query(default=None),
    z: str | None = Query(default=None),
):
    """兼容旧缓存的前端。新代码使用 /api/map/raster。"""
    return _handle_tile(request, x, y, z)


def _handle_tile(request: Request, x: str | None, y: str | None, z: str | None):
    if x is None or y is None or z is None:
        # 浏览器地址栏直接打开时给人看说明；Canvas/图片加载时给透明瓦片。
        accept = request.headers.get("accept", "").lower()
        if "text/html" in accept:
            return PlainTextResponse(_TILE_HINT)
        return _fallback_tile()

    parsed = (_parse_coord(x, -10_000_000, 10_000_000),
              _parse_coord(y, -10_000_000, 10_000_000),
              _parse_coord(z, 0, 20))
    if any(value is None for value in parsed):
        logger.warning("瓦片参数不合法：x=%r y=%r z=%r", x, y, z)
        return _fallback_tile()
    return _proxy_tile(*parsed)


def _parse_coord(value: str, minimum: int, maximum: int) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    if not minimum <= number <= maximum:
        return None
    return number


class _TileUpstreamError(RuntimeError):
    """瓦片上游不可用。外层统一转成透明图，不把异常抛给浏览器。"""


def _classify_tile_exception(exc: BaseException) -> BreakerOutcome:
    if isinstance(exc, _TileUpstreamError):
        return BreakerOutcome.FAILURE
    if isinstance(exc, (httpx.HTTPError, TimeoutError, OSError)):
        return BreakerOutcome.FAILURE
    return BreakerOutcome.IGNORE


def _proxy_tile(x: int, y: int, z: int) -> FileResponse:
    """代理一张瓦片；上游不可用或熔断时返回透明图。"""
    try:
        return get_breaker("amap:tile").call(
            _fetch_tile,
            x,
            y,
            z,
            classify_exception=_classify_tile_exception,
        )
    except CircuitOpenError:
        logger.warning("高德瓦片熔断中，返回透明瓦片")
        return _fallback_tile()
    except _TileUpstreamError as e:
        logger.warning("高德瓦片不可用，返回透明瓦片：%s", e)
        return _fallback_tile()


def _fetch_tile(x: int, y: int, z: int) -> FileResponse:
    host = TILE_HOSTS[(x + y + z) % len(TILE_HOSTS)]
    params = {
        "lang": "zh_cn",
        "size": 1,
        "scale": 1,
        "style": 8,
        "x": x,
        "y": y,
        "z": z,
    }

    response: httpx.Response | None = None
    with httpx.Client(timeout=TILE_TIMEOUT_SECONDS, follow_redirects=True) as client:
        # 上游偶尔会抖一下；重试一次比直接给用户一块空白划算。
        for attempt in range(2):
            try:
                candidate = client.get(f"https://{host}/appmaptile", params=params)
                candidate.raise_for_status()
                if candidate.content:
                    response = candidate
                    break
            except httpx.HTTPError as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                logger.warning(
                    "高德瓦片请求失败：%s status=%s attempt=%s",
                    type(e).__name__,
                    status,
                    attempt + 1,
                )
                if attempt == 0:
                    time.sleep(0.2)

    if response is None:
        raise _TileUpstreamError("高德瓦片请求失败")

    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("image/") or not response.content:
        logger.warning(
            "高德瓦片返回了非图片内容：content-type=%s length=%s",
            content_type,
            len(response.content),
        )
        raise _TileUpstreamError("高德瓦片返回了非图片内容")

    # 不缓存：底图内容不在本地留存，每次都实时向上游取。
    return FileResponse(
        content=response.content,
        media_type=content_type,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


def _fallback_tile() -> FileResponse:
    """透明瓦片兜底：让地图继续渲染，不去打断其它瓦片。"""
    return FileResponse(
        content=_TRANSPARENT_PNG,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "X-Tile-Fallback": "1",
        },
    )
