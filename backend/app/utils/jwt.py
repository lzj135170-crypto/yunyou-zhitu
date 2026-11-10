import jwt
from datetime import datetime, timedelta, timezone
from app.config import settings


def create_access_token(user_id: int) -> str:
    """ 创建JWT token """
    # ⚠️ 必须用带时区的 UTC 时间。datetime.now() 是“本地 naive 时间”，
    # PyJWT 会把 naive 的 exp 当成 UTC 处理；在东八区，这会让 24 小时
    # 有效期实际只有 16 小时。显式写 timezone.utc 才是真正的时间点。
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.JWT_EXPIRE_HOURS)

    payload = {"user_id": user_id, "exp": expire}

    return jwt.encode(
        payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )


def decode_access_token(token: str) -> dict:
    """ 解析JWT token """
    return jwt.decode(
        token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
    )
