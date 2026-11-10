from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'admin')", name="ck_users_role"),
        CheckConstraint(
            "status IN ('active', 'disabled', 'locked')",
            name="ck_users_status",
        ),
        # PostgreSQL 的唯一约束允许多个 NULL：没填邮箱/手机号的用户不会互相冲突。
        UniqueConstraint("email", name="uq_users_email"),
        UniqueConstraint("phone", name="uq_users_phone"),
        {"comment": "用户信息表"},
    )

    # 主键本表叫 user_id（不是 id），且自增：插入时不传，由数据库序列分配。
    # 其余各表的主键统一叫 id，Base 不再统一定义主键，所以这里必须自己声明。
    user_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True, comment="用户ID")

    # unique=True 交给数据库兜底：注册接口虽有查重，但"先查再插"之间存在竞态窗口，
    # 两个并发请求可能同时查到"账号不存在"然后都插入成功。唯一约束能彻底堵死这种情况。
    username: Mapped[str] = mapped_column(String(50), comment="账号", nullable=False, unique=True)

    # 列名必须叫 password_hash，不能叫 password：这里存的是 bcrypt 哈希，
    # 不是明文。列名写清楚能避免后来的人误以为可以直接比对。
    password_hash: Mapped[str] = mapped_column(String(255), comment="密码哈希", nullable=False)

    # 角色只允许 user/admin 两种。真实业务里角色往往还会拆成独立的权限表，
    # 本轮先保持单角色；CHECK 约束至少能阻止 'student' 这种历史值再混进来。
    role: Mapped[str] = mapped_column(
        String(20), comment="角色: user-用户, admin-管理员",
        nullable=False, default="user", server_default="user",
    )

    # 邮箱/手机号是常见的登录/找回凭证，所以要求唯一；两者都可为空。
    # 邮箱长度按真实业务放宽到 255，手机号 32 足够容纳国际号码。
    email: Mapped[str | None] = mapped_column(String(255), comment="邮箱")
    phone: Mapped[str | None] = mapped_column(String(32), comment="手机号")
    avatar: Mapped[str | None] = mapped_column(String(500), comment="头像地址")

    # 状态用字符串而不是 0/1：数据库里看一眼就知道是什么意思，
    # 也避免各接口对 0 和 1 的含义理解不一致。locked 预留给风控/密码错误锁定。
    status: Mapped[str] = mapped_column(
        String(20), comment="状态: active-正常, disabled-禁用, locked-锁定",
        nullable=False, default="active", server_default="active",
    )

    # 最近一次登录成功的时间。登录接口 best-effort 更新，写失败不影响发 token。
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="最近登录时间"
    )
