"""Alembic 运行环境。

连接串不写在 alembic.ini 里，而是从 app.config.settings.DATABASE_URL 读取，
避免数据库密码进入版本库。日志里也不会回显连接串。
"""

from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context

from app.config import settings
from app.database import Base
import app.models  # noqa: F401  触发所有模型注册到 Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# 这些表不属于业务 ORM metadata，而是 LangGraph 检查点/迁移版本自己维护的。
# 如果不排除，autogenerate 会把它们当成“数据库里多出来的表”，生成 drop_table，
# 一执行就清空多轮对话记忆。
_IGNORED_TABLES = {
    "alembic_version",
    "checkpoints",
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoint_migrations",
}


def _include_object(object_, name, type_, reflected, compare_to):
    if type_ == "table" and name in _IGNORED_TABLES:
        return False
    return True


def _configure(connection=None, url=None) -> None:
    kwargs = dict(
        target_metadata=target_metadata,
        include_object=_include_object,
        compare_type=True,
        compare_server_default=True,
        # Alembic 1.20 把注释比较做成了插件。注释只是文档，不参与业务 schema
        # 差异判断；不排除它的话，历史库里几条旧注释会让 alembic check 一直报错。
        autogenerate_plugins=[
            "alembic.autogenerate.*",
            "~alembic.autogenerate.comments",
        ],
    )
    if connection is not None:
        kwargs["connection"] = connection
    else:
        kwargs["url"] = url
        kwargs["literal_binds"] = True
        kwargs["dialect_opts"] = {"paramstyle": "named"}
    context.configure(**kwargs)


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连数据库。"""
    _configure(url=settings.DATABASE_URL)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：直接从 settings 建 Engine，不复用 app.database.engine。"""
    connectable = create_engine(settings.DATABASE_URL, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        _configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
