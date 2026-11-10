"""追踪查询接口的响应模型。

刻意只暴露排查需要的字段：
- run 摘要（状态、耗时、token、错误码）
- span 树（名称、类型、父子关系、耗时、token、安全错误摘要、白名单属性）

不返回 agent_runs.input / output：里面有用户原始提问、各专家产出和 RAG 上下文，
查询追踪不应该顺带把聊天内容带出来。
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class TraceRun(BaseModel):
    """一次 agent_run 的摘要。"""

    run_id: int
    trace_id: str
    status: str
    agent_name: str
    total_tokens: int
    latency_ms: int
    create_time: datetime
    finished_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None


class TraceSpan(BaseModel):
    """一个 span 的对外表示；父子关系由 parent_span_id 表达。"""

    span_id: str
    parent_span_id: str | None = None
    name: str
    span_type: str
    node_name: str | None = None
    status: str
    start_time: datetime
    end_time: datetime | None = None
    duration_ms: int
    tokens: int
    error_code: str | None = None
    error_message: str | None = None
    attributes: dict[str, Any] | None = None


class TraceResponse(BaseModel):
    run: TraceRun
    spans: list[TraceSpan]
