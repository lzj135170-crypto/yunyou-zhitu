from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreateRequest(BaseModel):
    """新建会话的请求体。title 可以不传。

    用户点"新建对话"时通常还没想好叫什么，允许先开一个空白会话，
    标题留给首条消息自动生成（见 app/services/message_service.py 的 send_message）。
    """

    # 自动去掉首尾空白，避免客户端传进来一串空格被当成有效标题存进库
    model_config = ConfigDict(str_strip_whitespace=True)

    # 长度上限与 Conversation.title 的列宽 String(255) 严格对齐。
    # 不设上限的话，超长标题会一路进到数据库才报 value too long，用户看到的是 500。
    title: str | None = Field(default=None, max_length=255, description="会话标题")


class ConversationUpdateRequest(BaseModel):
    """重命名会话的请求体。

    只开放 title 一个字段。会话归属（user_id）绝不接受客户端传入——
    那等于让调用方自己声明身份，任何人改一下请求体就能操作别人的会话。
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    # min_length=1 挡住"把标题改成空字符串或纯空白"。
    # 空标题应该用"不传"表达，而不是"传一个空串"。
    title: str = Field(min_length=1, max_length=255, description="新的会话标题")


class ConversationResponse(BaseModel):
    """会话的对外表示。

    不含 user_id：调用方必然是会话所有者（接口层已按 JWT 里的 user_id 过滤过），
    客户端不需要这个内部 id，少暴露一个能被用来探测的字段。
    """

    id: int
    title: str | None = None
    create_time: datetime
    update_time: datetime

    # from_attributes 让 Pydantic 可以直接读 ORM 对象的属性，不用手工组装 dict
    model_config = ConfigDict(from_attributes=True)
