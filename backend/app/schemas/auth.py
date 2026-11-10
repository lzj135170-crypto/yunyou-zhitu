from pydantic import BaseModel, ConfigDict, Field

from app.schemas.user import UserResponse


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user: UserResponse


class RegisterRequest(BaseModel):
    """注册请求。字段与 User 模型对齐，但刻意不包含 role 和 status。

    不开放 role：如果允许客户端传 role，任何人构造一个 {"role": "admin"} 就能
    把自己注册成管理员，这是提权漏洞。服务端固定写死为普通用户。

    不开放 status：新注册账号一律是可用的，没有让客户端决定的道理。
    """

    # 开启后自动去掉首尾空格，避免 " zhangsan" 和 "zhangsan" 被当成两个账号。
    # 在加了唯一约束之后这一点尤其重要，否则用户输错一个空格就登不上自己的号。
    model_config = ConfigDict(str_strip_whitespace=True)

    username: str = Field(min_length=3, max_length=50, description="账号")
    # 长度下限按项目现有约定设为 6（seed_users.py 里的测试账号密码就是 6 位）。
    # 上限 64 是为了防止超长字符串拖慢 bcrypt —— bcrypt 只取前 72 字节，再长也没有意义。
    password: str = Field(min_length=6, max_length=64, description="密码")
    # 邮箱和手机号可选，长度上限与 User 模型的列宽保持一致，避免入库时被数据库截断报错。
    email: str | None = Field(default=None, max_length=255, description="邮箱")
    phone: str | None = Field(default=None, max_length=32, description="手机号")
