from typing import Any

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UserPreference(Base):
    """用户偏好表：存用户的口味和出行习惯，供智能体做个性化推荐。

    为什么偏好内容整体塞进一个 JSONB，而不是拆成"喜欢的城市""预算上限""忌口"等一堆列：
    偏好项会随着业务不断增删（今天加个"是否接受红眼航班"，明天加个"酒店星级偏好"），
    每加一项就改一次表结构成本很高；JSONB 可以随时塞新字段，不用动表定义。
    代价是数据库不知道里面有哪些字段，没法做强制校验，这个由 Pydantic schema 在接口层兜住。
    """

    __tablename__ = "user_preferences"
    __table_args__ = {"comment": "用户偏好表"}

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="主键ID")

    # 所属用户ID。注意 users 表的主键叫 user_id 而不是 id。
    # unique=True：一个用户只保留一条偏好记录，偏好是"覆盖式"更新的，
    #   设成唯一等于让数据库帮忙挡住"同一用户存出两条互相矛盾的偏好"这种 bug。
    # ondelete="CASCADE"：本项目不做用户注销功能，属防御性设计，
    #   防止手工删库或测试清理数据时留下孤立记录。
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"),
        comment="所属用户ID",
        nullable=False,
        unique=True,
        index=True,
    )

    # 偏好内容本身，形如 {"city": "成都", "budget": 3000, "tags": ["美食", "慢节奏"]}。
    # 允许为空：理论上不会，但如果只有一个空对象也没必要强制报错。
    preferences: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, comment="偏好内容(JSON)"
    )
