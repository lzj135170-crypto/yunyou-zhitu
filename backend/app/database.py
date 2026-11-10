from sqlalchemy.orm import sessionmaker, DeclarativeBase, Mapped, mapped_column
from sqlalchemy import create_engine, DateTime, func
from app.config import settings
from datetime import datetime, timezone

# PostgreSQL 连接池；pool_pre_ping 避免取到数据库重启后失效的死连接。
engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)

# 数据库 Session 工厂。
SessionLocal = sessionmaker(bind=engine, autoflush=False)


def get_db():
    """FastAPI 依赖注入的数据库会话；请求结束后归还连接。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        # 必须调用 close()，否则连接不会归还连接池。
        db.close()


class Base(DeclarativeBase):
    """所有模型的基类，只放公共时间戳字段；主键由各模型声明，避免命名和外键冲突。

    时间统一使用带时区的 TIMESTAMPTZ，Python 侧写 UTC，数据库侧保留 server_default。
    """
    create_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        comment="创建时间",
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        comment="更新时间",
    )

