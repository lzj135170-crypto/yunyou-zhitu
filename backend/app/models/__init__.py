"""模型包入口。

逐个导入所有模型，确保类注册到 Base.metadata，供 Alembic 和测试读取。
新增模型文件时必须在这里补一行 import，否则迁移会静默漏表。
"""

from app.models.agent_run import AgentRun
from app.models.agent_run_span import AgentRunSpan
from app.models.conversation import Conversation
from app.models.document import Document
from app.models.message import Message
from app.models.trip import Trip
from app.models.trip_day import TripDay
from app.models.trip_item import TripItem
from app.models.user import User
from app.models.user_preference import UserPreference

__all__ = [
    "AgentRun",
    "AgentRunSpan",
    "Conversation",
    "Document",
    "Message",
    "Trip",
    "TripDay",
    "TripItem",
    "User",
    "UserPreference",
]
