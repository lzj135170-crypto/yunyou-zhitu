from pydantic import BaseModel, ConfigDict

class UserResponse(BaseModel):
    # 字段名要和 User 模型对齐：users 表的主键是 user_id，不是 id
    user_id: int
    username: str
    role: str
    email: str | None = None
    phone: str | None = None
    # 状态从 int(0/1) 改为字符串（active/disabled/locked）。
    # 前端目前不展示这个字段，但接口语义更明确。
    status: str

    model_config = ConfigDict(from_attributes=True)

