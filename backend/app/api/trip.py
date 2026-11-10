"""结果页的数据接口：行程投影 + 静态地图。

两个接口都挂在 /conversations/{id} 下面，和消息一样属于会话的子资源。
这样"先校验会话归属"是 URL 结构带来的，不需要每个接口各写一遍。
"""

import base64
import logging

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response as FileResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.common import cache
from app.common.exceptions import BusinessException
from app.common.response import Response
from app.config import settings
from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.trip import (
    TripHotelArea,
    TripHotelPlace,
    TripHotelTier,
    TripHotelsResponse,
    TripMediaResponse,
    TripPhoto,
    TripPlace,
    TripRestaurantPlace,
    TripRestaurantsResponse,
    TripRoutesRequest,
)
from app.services import amap_map, trip_route_service, trip_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/conversations/{conversation_id}",
    tags=["行程"],
)


@router.get("/plan")
def get_latest_plan(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """取这个会话最近一次成功运行对应的行程数据。

    只读、不调模型：结果页刷新、历史列表点进来都走这里，
    不会因为多看一眼就再烧一轮 token。
    """
    plan = trip_service.get_latest_plan(db, conversation_id, current_user.user_id)
    return Response.success(message="请求成功", data=plan)


@router.get("/hotels")
def get_trip_hotels(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """在模型给的住宿区域下，补上高德检索到的真实酒店。

    单独一个接口而不是塞进 /media：酒店搜索要多打好几次高德，
    和地图/景点图混在一起会让结果页首屏等更久。
    单区域失败只写在对应区域的 error 里，其他区域照常返回。
    """
    plan = trip_service.get_latest_plan(db, conversation_id, current_user.user_id)
    specs = [
        amap_map.HotelAreaSpec(area=item.area, level=item.level)
        for item in plan.hotels
    ]

    # 酒店检索要走多次高德，且同一城市的区域组合经常重复。
    # 按「目的地 + 区域/档位」缓存；行程改了区域就自然换 key。
    key = cache.hotels_key(
        plan.destination, [(item.area, item.level) for item in specs]
    )
    cached = cache.get_json(key)
    if cached is not None:
        try:
            return Response.success(
                message="请求成功",
                data=TripHotelsResponse.model_validate(cached),
            )
        except (ValidationError, TypeError, ValueError):
            cache.delete(key)

    try:
        results, warnings = amap_map.search_area_hotels(plan.destination, specs)
    except amap_map.AmapNotConfigured as e:
        raise BusinessException(message=str(e), code=404) from None
    except amap_map.AmapUpstreamError as e:
        # 正常情况下单区域异常已经落在 result.error 里，这里只兜底整体故障。
        logger.warning("检索住宿酒店失败：%s infocode=%s", e, e.infocode)
        raise BusinessException(
            message="酒店信息暂时不可用，请稍后重试", code=502
        ) from None

    data = TripHotelsResponse(
        destination=plan.destination,
        areas=[
            TripHotelArea(
                index=item.index,
                area=item.area,
                level=item.level,
                places=[_hotel_place_out(place) for place in item.places],
                tiers=[
                    TripHotelTier(
                        key=tier.key,
                        label=tier.label,
                        places=[_hotel_place_out(place) for place in tier.places],
                        error=tier.error,
                    )
                    for tier in item.tiers
                ],
                error=item.error,
            )
            for item in results
        ],
        warnings=warnings,
    )

    # 部分区域失败或完全没有结果时不缓存，下一次打开还能重试。
    has_places = any(tier.places for area in data.areas for tier in area.tiers)
    if not warnings and has_places:
        cache.set_json(
            key,
            data.model_dump(mode="json"),
            settings.RESULT_CACHE_TTL_SECONDS,
        )

    return Response.success(message="请求成功", data=data)


def _hotel_place_out(place: amap_map.HotelPlace) -> TripHotelPlace:
    """服务层的 HotelPlace 转成接口模型；字段一一对应，不做任何补造。"""
    return TripHotelPlace(
        name=place.name,
        longitude=place.longitude,
        latitude=place.latitude,
        address=place.address,
        photo_url=place.photo_url,
        rating=place.rating,
        category=place.category,
        business_area=place.business_area,
        avg_cost=place.avg_cost,
    )


@router.get("/restaurants")
def get_trip_restaurants(
    conversation_id: int,
    keyword: str = Query(
        ...,
        min_length=1,
        max_length=100,
        description="美食名称，如「火锅」「沙茶面」",
    ),
    area: str = Query(
        default="",
        max_length=200,
        description="美食卡片上的区域描述，如「文殊院一带（洞子口张老二凉粉）」",
    ),
    limit: int = Query(default=5, ge=1, le=10),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """按美食名称 + 区域查高德真实餐厅。

    结果页点开某张美食卡片时才调用；只读、不落库。单次失败不影响行程本身。
    """
    plan = trip_service.get_latest_plan(db, conversation_id, current_user.user_id)
    if not plan.destination:
        raise BusinessException(message="行程缺少目的地，无法搜索餐厅", code=422)

    try:
        places, warnings = amap_map.search_restaurants(
            plan.destination, keyword, area, limit
        )
    except amap_map.AmapNotConfigured as e:
        raise BusinessException(message=str(e), code=404) from None
    except ValueError as e:
        raise BusinessException(message=str(e), code=422) from None
    except amap_map.AmapUpstreamError as e:
        logger.warning("搜索餐厅失败：%s infocode=%s", e, e.infocode)
        raise BusinessException(
            message="餐厅信息暂时不可用，请稍后重试", code=502
        ) from None

    return Response.success(
        message="请求成功",
        data=TripRestaurantsResponse(
            destination=plan.destination,
            keyword=keyword,
            area=area,
            places=[_restaurant_place_out(place) for place in places],
            warnings=warnings,
        ),
    )


def _restaurant_place_out(
    place: amap_map.RestaurantPlace,
) -> TripRestaurantPlace:
    """服务层的 RestaurantPlace 转成接口模型；字段一一对应，不做补造。"""
    return TripRestaurantPlace(
        name=place.name,
        longitude=place.longitude,
        latitude=place.latitude,
        address=place.address,
        photo_url=place.photo_url,
        rating=place.rating,
        category=place.category,
        business_area=place.business_area,
        avg_cost=place.avg_cost,
    )


@router.post("/routes")
def build_trip_routes(
    conversation_id: int,
    payload: TripRoutesRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """按景点顺序计算真实道路路线，并按每日行程文字分组。

    景点先按 S07 的日程匹配分归到各天（未匹配的按位置并入最近一天），
    组内顺序暂时保持请求顺序，S09 再做"近的优先"重排。
    单段失败会降级成直线估算并写进对应 day.warnings，整体仍然返回 200。
    """
    plan = trip_service.get_latest_plan(db, conversation_id, current_user.user_id)

    if not plan.attractions:
        raise BusinessException(
            message="这次行程没有景点，无法规划路线", code=422
        )
    if not payload.places:
        raise BusinessException(message="请至少提供一个景点", code=422)

    try:
        result = trip_route_service.build_routes(plan, payload)
    except amap_map.AmapNotConfigured as e:
        raise BusinessException(message=str(e), code=404) from None
    except ValueError as e:
        # 传进来的点都不在 plan.attractions 里，或都没有坐标。
        raise BusinessException(message=str(e), code=422) from None
    except amap_map.AmapUpstreamError as e:
        logger.warning("生成路线失败：%s infocode=%s", e, e.infocode)
        raise BusinessException(
            message="路线暂时不可用，请稍后重试", code=502
        ) from None

    return Response.success(message="请求成功", data=result)


@router.get("/media")
def get_trip_media(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """一次返回静态地图 + 景点图片，供结果页使用。

    地图和图片来自同一次 POI 搜索，避免 /map.png 和图片接口各查一遍高德，
    把个人 key 的 QPS 打满。图片 URL 是公开 CDN 地址，不含 key。
    """
    plan = trip_service.get_latest_plan(db, conversation_id, current_user.user_id)

    try:
        png, places = amap_map.render_plan_media(
            plan.destination,
            [item.name for item in plan.attractions],
        )
    except amap_map.AmapNotConfigured as e:
        raise BusinessException(message=str(e), code=404) from None
    except amap_map.AmapUpstreamError as e:
        logger.warning("生成行程媒体失败：%s infocode=%s", e, e.infocode)
        raise BusinessException(message="地图暂时不可用，请稍后重试", code=502) from None

    return Response.success(
        message="请求成功",
        data=TripMediaResponse(
            map_png=base64.b64encode(png).decode("ascii"),
            places=[
                TripPlace(
                    name=place.name,
                    longitude=place.longitude,
                    latitude=place.latitude,
                    address=place.address,
                    photo_url=place.photo_url,
                )
                for place in places
            ],
            photos=[
                TripPhoto(name=place.name, url=place.photo_url, address=place.address)
                for place in places
                if place.photo_url
            ],
        ),
    )


@router.get("/map.png")
def get_static_map(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """按行程里的景点名称生成一张高德静态地图。

    ⚠️ key 只在后端使用。前端拿到的是 PNG，不是带 key 的地图 URL。
    ⚠️ 不缓存：高德条款不允许把地图内容存到服务之外，这里每次实时取。
    """
    plan = trip_service.get_latest_plan(db, conversation_id, current_user.user_id)

    try:
        png = amap_map.render_static_map(
            plan.destination,
            [item.name for item in plan.attractions],
        )
    except amap_map.AmapNotConfigured as e:
        # 没配 key / 开关关掉是"这个功能没启用"，不是服务器错误。
        raise BusinessException(message=str(e), code=404) from None
    except amap_map.AmapUpstreamError as e:
        logger.warning("生成静态地图失败：%s infocode=%s", e, e.infocode)
        raise BusinessException(message="地图暂时不可用，请稍后重试", code=502) from None

    # no-store 同时满足两点：浏览器不缓存、高德数据不在客户端留副本。
    return FileResponse(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "no-store, max-age=0"},
    )
