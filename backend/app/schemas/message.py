from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class MessageSendRequest(BaseModel):
    """发消息的请求体。"""

    # str_strip_whitespace 让"只含空白字符"的内容在长度校验之前就被剥成空串，
    # 再由 min_length=1 挡下（返回 422）。
    # 好处是服务层不用再写一遍空值判断——校验只放 Schema 这一处，不会出现两套规则。
    model_config = ConfigDict(str_strip_whitespace=True)

    # 上限 8000：content 对应的是 Text 列，本身不限长，
    # 但对外开放的接口必须有上限，否则一次请求就能往库里塞几 MB，
    # 后续拼多轮上下文时会直接把提示词撑爆。
    content: str = Field(min_length=1, max_length=8000, description="消息正文")


class MessageResponse(BaseModel):
    """消息的对外表示。

    只列 messages 表自己的列，刻意不嵌套 conversation——
    响应模型是防 N+1 的白名单，嵌套字段就是懒加载的入口，
    一旦加上，列表接口立刻变成 1+N 条 SQL。

    时间戳只给 create_time：本轮消息不支持编辑，update_time 对外没有意义。
    """

    id: int
    conversation_id: int
    role: str
    content: str
    agent_name: str | None = None
    create_time: datetime

    model_config = ConfigDict(from_attributes=True)


class MessageListData(BaseModel):
    """消息列表的分页壳。

    用游标分页而不是页码：客户端拿 items 里最早一条的 id 作为 before_id 再请求一次，
    就能一直往前翻历史。这样在聊天过程中有人发新消息时，翻页不会错位。
    """

    items: list[MessageResponse]
    has_more: bool


class MessageSendData(BaseModel):
    """一次发送的完整结果。

    用户消息也一并返回：不做乐观更新的客户端可以直接把两条都渲染出来，
    不必自己猜服务端生成的自增 id 和时间戳。

    trace_id 和响应头 X-Trace-ID 是同一个值；放进 body 是为了让前端
    不用专门读响应头也能直接拼追踪查询 URL。
    """

    user_message: MessageResponse
    assistant_message: MessageResponse
    trace_id: str | None = None
