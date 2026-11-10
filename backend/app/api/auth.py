import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, RegisterRequest
from app.schemas.user import UserResponse
from app.utils.password import hash_password, verity_password
from app.utils.jwt import create_access_token
from app.common.response import Response
from app.common.exceptions import BusinessException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["权限验证"])


@router.post("/login")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    # 根据用户账号查询数据库
    user = db.query(User).filter(User.username == data.username).first()

    # 判断账号和密码是否正确。列名是 password_hash，不能再写成 user.password。
    # 这是认证失败，不是服务器故障：显式给 401，别落到 BusinessException 的默认 500。
    if not user or not verity_password(data.password, user.password_hash):
        raise BusinessException(message="账号或密码错误", code=401)
    # 验证账号状态
    if user.status != "active":
        raise BusinessException(message="账号被禁用", code=403)

    # 记录最近登录时间。这是审计信息，写失败不该阻止用户登录，
    # 所以单独 commit，失败只回滚这次更新并记 warning。
    try:
        user.last_login_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("更新最近登录时间失败（用户 %s）", user.user_id, exc_info=True)

    # 生成token
    token = create_access_token(user.user_id)
    # 返回信息
    return Response.success(
        message="登录成功",
        data=LoginResponse(token=token, user=UserResponse.model_validate(user)),
    )


@router.post("/register")
def register(data: RegisterRequest, db: Session = Depends(get_db)):
    # 先查一遍账号是否已存在，这样能给出"账号已存在"这种明确提示，
    # 而不是等数据库抛唯一约束冲突再兜底（那样错误信息没法区分是账号重复还是别的冲突）。
    if db.query(User).filter(User.username == data.username).first():
        raise BusinessException(message="账号已存在", code=409)

    # 邮箱统一小写、空串转 NULL：唯一约束下 'A@x.com' 和 'a@x.com' 应该是同一个账号。
    email = (data.email or "").strip().lower() or None
    phone = (data.phone or "").strip() or None
    if email and db.query(User).filter(func.lower(User.email) == email).first():
        raise BusinessException(message="邮箱已被使用", code=409)
    if phone and db.query(User).filter(User.phone == phone).first():
        raise BusinessException(message="手机号已被使用", code=409)

    user = User(
        username=data.username,
        # 只存 bcrypt 哈希，明文绝不入库。登录时由 verity_password 比对。
        password_hash=hash_password(data.password),
        # 角色固定写死，不接受客户端传入，防止有人把自己注册成管理员
        role="user",
        email=email,
        phone=phone,
        status="active",   # 新账号默认可用
    )
    db.add(user)

    try:
        db.commit()
    except IntegrityError:
        # 走到这里说明上面的查重和这次提交之间，有另一个请求抢先插入了同名账号
        # 或同一个邮箱/手机号。唯一约束在这里起到了兜底作用。
        db.rollback()
        if db.query(User).filter(User.username == data.username).first():
            raise BusinessException(message="账号已存在", code=409) from None
        raise BusinessException(message="邮箱或手机号已被使用", code=409) from None

    # commit 之后对象已过期，读 user.user_id 会触发一次查询把它刷回来。
    # 拿到 id 才能签发 token，所以这行不能省。
    db.refresh(user)

    token = create_access_token(user.user_id)

    return Response.success(
        message="注册成功",
        data=LoginResponse(token=token, user=UserResponse.model_validate(user)),
    )
