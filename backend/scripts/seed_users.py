"""向 users 表灌入模拟数据（开发/联调自测用）。

用法（在 backend 目录下执行）：
    .venv/Scripts/python.exe scripts/seed_users.py

设计要点：
1. 幂等：按 username 判断，已存在的账号会跳过，不会报错也不会插重复。
   所以这个脚本可以反复执行，随时补齐缺失的账号。
2. 密码统一走 hash_password 做 bcrypt 加密后再入库。绝对不能直接写明文——
   users.password_hash 存的必须是 "$2b$12$..." 这种哈希串，登录接口会用
   verity_password 拿哈希校验，存明文会导致永远登录失败。
3. 初始密码由 SEED_DEFAULT_PASSWORD 环境变量提供；默认只创建普通用户，
   需要 admin 账号时显式设置 SEED_ADMIN=1。
"""

# 把 backend 根目录加入模块搜索路径，这样才能 import app 包。
# 否则直接执行 scripts/seed_users.py 时，Python 只会把 scripts/ 当作搜索起点，
# 报 ModuleNotFoundError: No module named 'app'。
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.utils.password import hash_password  # noqa: E402

# 所有模拟账号的统一初始密码。必须由调用者通过环境变量提供，
# 不在仓库里写死弱密码；未设置时 seed_users() 会直接退出。
DEFAULT_PASSWORD = os.environ.get("SEED_DEFAULT_PASSWORD", "")

# 模拟用户清单。字段含义与 User 模型一致：
#   username 账号（登录用，需唯一）
#   role     角色：admin-管理员，user-普通用户
#   email    邮箱，可为空
#   phone    手机号，可为空
#   avatar   头像地址，可为空
#   status   状态：active-正常，disabled-禁用
# 数据里刻意留了一个禁用账号（student06）和一个缺少邮箱手机号的账号（student05），
# 方便测试登录接口的"账号被禁用"分支以及字段为空时的序列化表现。
SEED_USERS = [
    {
        "username": "admin",
        "role": "admin",
        "email": "admin@lab.com",
        "phone": "13800000001",
        "avatar": "/static/avatar/admin.png",
        "status": "active",
    },
    {
        "username": "admin02",
        "role": "admin",
        "email": "admin02@lab.com",
        "phone": "13800000002",
        "avatar": None,
        "status": "active",
    },
    # user01/user02/user03 是 smoke_chat_flow.py 和 seed_chat_data.py 依赖的账号。
    # 原 seed 只建了 student 账号，导致在新库上跑会话种子/冒烟时会找不到用户。
    {
        "username": "user01",
        "role": "user",
        "email": "user01@lab.com",
        "phone": "13700000001",
        "avatar": None,
        "status": "active",
    },
    {
        "username": "user02",
        "role": "user",
        "email": "user02@lab.com",
        "phone": "13700000002",
        "avatar": None,
        "status": "active",
    },
    {
        "username": "user03",
        "role": "user",
        "email": "user03@lab.com",
        "phone": "13700000003",
        "avatar": None,
        "status": "active",
    },
    {
        "username": "student01",
        "role": "user",
        "email": "student01@lab.com",
        "phone": "13911110001",
        "avatar": "/static/avatar/01.png",
        "status": "active",
    },
    {
        "username": "student02",
        "role": "user",
        "email": "student02@lab.com",
        "phone": "13911110002",
        "avatar": None,
        "status": "active",
    },
    {
        "username": "student03",
        "role": "user",
        "email": "student03@lab.com",
        "phone": "13911110003",
        "avatar": None,
        "status": "active",
    },
    {
        "username": "student04",
        "role": "user",
        "email": "student04@lab.com",
        "phone": "13911110004",
        "avatar": None,
        "status": "active",
    },
    {
        "username": "student05",
        "role": "user",
        "email": None,
        "phone": None,
        "avatar": None,
        "status": "active",
    },
    {
        "username": "student06",
        "role": "user",
        "email": "student06@lab.com",
        "phone": "13911110006",
        "avatar": None,
        "status": "disabled",
    },
]


def seed_users() -> tuple[list[str], list[str]]:
    """把 SEED_USERS 写入数据库。

    返回 (本次新增的账号列表, 因已存在而跳过的账号列表)。
    整个过程放在一个事务里：任意一条失败就整体回滚，避免只插进去一半的脏数据。

    初始密码来自 SEED_DEFAULT_PASSWORD；默认不创建 admin 账号，
    需要时显式设置 SEED_ADMIN=1。
    """
    if not DEFAULT_PASSWORD:
        raise SystemExit(
            "请先设置 SEED_DEFAULT_PASSWORD 环境变量，再执行 scripts/seed_users.py"
        )

    include_admin = os.environ.get("SEED_ADMIN") == "1"
    users = (
        SEED_USERS
        if include_admin
        else [item for item in SEED_USERS if item.get("role") != "admin"]
    )

    db = SessionLocal()
    created: list[str] = []
    skipped: list[str] = []

    try:
        for item in users:
            username = item["username"]

            # 幂等判断：账号已存在就跳过，不覆盖已有数据（避免把测试中改过的密码重置掉）
            if db.query(User).filter(User.username == username).first():
                skipped.append(username)
                continue

            # 邮箱/手机号现在是唯一列。老库里这些值可能已经被别的账号占用，
            # 直接插入会让整批 seed 回滚；这里提前查一次，冲突就跳过。
            email = (item.get("email") or "").strip().lower()
            phone = (item.get("phone") or "").strip()
            if email and db.query(User).filter(func.lower(User.email) == email).first():
                skipped.append(username)
                continue
            if phone and db.query(User).filter(User.phone == phone).first():
                skipped.append(username)
                continue

            # 注意 password_hash 是单独计算的：库里的密码永远是哈希值，不是明文
            db.add(User(password_hash=hash_password(DEFAULT_PASSWORD), **item))
            created.append(username)

        db.commit()
    except Exception:
        # 任何异常都回滚，保证不会留下插了一半的数据
        db.rollback()
        raise
    finally:
        # 一定要带括号调用，否则连接不会归还连接池
        db.close()

    return created, skipped


if __name__ == "__main__":
    created, skipped = seed_users()

    print(f"新增 {len(created)} 个账号：{', '.join(created) if created else '无'}")
    print(f"跳过 {len(skipped)} 个已存在账号：{', '.join(skipped) if skipped else '无'}")
    print("初始密码来自 SEED_DEFAULT_PASSWORD 环境变量。")
    print("需要创建 admin 账号时，设置 SEED_ADMIN=1 后重新执行。")
