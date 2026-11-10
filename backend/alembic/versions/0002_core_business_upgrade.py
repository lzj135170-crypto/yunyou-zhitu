"""core business upgrade

把 6 张基础表升级成可解释的业务模型：
- 时间戳统一为 timestamptz；
- users 拆出 password_hash、状态改字符串、加唯一约束；
- conversations 加软删除/计数/最后消息时间；
- messages 加 role 约束和复合索引；
- agent_runs 关联用户/请求消息/回复消息，加 trace_id/错误字段；
- documents 把 metadata 里常用的字段提升为列。

⚠️ 这里刻意不用 Autogenerate 的原样输出：自动生成会把 password→password_hash、
tokens→total_tokens 渲染成“新增列 + 删除旧列”，直接丢数据；也会在已有数据的表上
直接加 NOT NULL 列。本文件按“可空加列 → 回填 → SET NOT NULL → 加约束”的顺序手写。

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-26 10:31:31.137373
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES = (
    "users",
    "conversations",
    "messages",
    "agent_runs",
    "documents",
    "user_preferences",
)


def _to_timestamptz(table: str) -> None:
    """把 create_time/update_time 从本地 naive 时间转成带时区时间。

    现有数据是应用服务器本地时间（Asia/Shanghai），数据库时区也已确认是
    Asia/Shanghai。用显式 USING 转换，不依赖会话时区。
    """
    for column in ("create_time", "update_time"):
        op.alter_column(
            table,
            column,
            existing_type=postgresql.TIMESTAMP(),
            type_=sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            existing_nullable=False,
            postgresql_using=f"{column} AT TIME ZONE 'Asia/Shanghai'",
        )


def _to_naive_timestamp(table: str) -> None:
    """downgrade 用：timestamptz 转回本地 naive 时间。"""
    for column in ("create_time", "update_time"):
        # 先单独 DROP DEFAULT，再改类型：把 server_default=None 和 type_ 放在
        # 同一条 ALTER 里时，PostgreSQL 可能在删除默认值之前就校验类型转换。
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            server_default=None,
            existing_nullable=False,
        )
        op.alter_column(
            table,
            column,
            existing_type=sa.DateTime(timezone=True),
            type_=postgresql.TIMESTAMP(),
            existing_nullable=False,
            postgresql_using=f"{column} AT TIME ZONE 'Asia/Shanghai'",
        )


def upgrade() -> None:
    """Upgrade schema."""
    # ------------------------------------------------------------------
    # 1. 时间戳统一为 timestamptz（必须先做：后面多个回填都从旧时间列取值）
    # ------------------------------------------------------------------
    for table in _TABLES:
        _to_timestamptz(table)

    # ------------------------------------------------------------------
    # 2. users
    # ------------------------------------------------------------------
    # 重命名而不是新增+删除，bcrypt 哈希原样保留。
    op.alter_column(
        "users",
        "password",
        new_column_name="password_hash",
        existing_type=sa.String(length=255),
        existing_nullable=False,
    )
    op.alter_column(
        "users", "email",
        existing_type=sa.String(length=50), type_=sa.String(length=255),
        existing_nullable=True,
    )
    op.alter_column(
        "users", "phone",
        existing_type=sa.String(length=50), type_=sa.String(length=32),
        existing_nullable=True,
    )
    op.alter_column(
        "users", "avatar",
        existing_type=sa.String(length=50), type_=sa.String(length=500),
        existing_nullable=True,
    )
    op.add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True,
                  comment="最近登录时间"),
    )

    # 空字符串没有业务含义，统一成 NULL；邮箱统一小写，避免大小写重复。
    op.execute("UPDATE users SET email = lower(btrim(email)) WHERE email IS NOT NULL")
    op.execute("UPDATE users SET phone = btrim(phone) WHERE phone IS NOT NULL")
    op.execute("UPDATE users SET email = NULL WHERE email = ''")
    op.execute("UPDATE users SET phone = NULL WHERE phone = ''")

    # 历史值映射：student 等旧角色统一归为 user；0/1 状态转为字符串。
    op.execute("UPDATE users SET role = 'user' WHERE role NOT IN ('user', 'admin')")
    op.alter_column(
        "users", "role",
        existing_type=sa.String(length=50), type_=sa.String(length=20),
        server_default=sa.text("'user'"), existing_nullable=False,
    )
    op.alter_column(
        "users", "status",
        existing_type=sa.Integer(), type_=sa.String(length=20),
        server_default=sa.text("'active'"),
        existing_nullable=False,
        postgresql_using=(
            "CASE status WHEN 1 THEN 'active' WHEN 0 THEN 'disabled' "
            "ELSE 'disabled' END"
        ),
    )
    op.create_check_constraint(
        "ck_users_role", "users", "role IN ('user', 'admin')"
    )
    op.create_check_constraint(
        "ck_users_status", "users",
        "status IN ('active', 'disabled', 'locked')",
    )
    op.create_unique_constraint("uq_users_email", "users", ["email"])
    op.create_unique_constraint("uq_users_phone", "users", ["phone"])

    # ------------------------------------------------------------------
    # 3. conversations
    # ------------------------------------------------------------------
    op.add_column(
        "conversations",
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default=sa.text("'active'"),
                  comment="状态: active-正常, archived-归档"),
    )
    op.add_column(
        "conversations",
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True,
                  comment="最后一条消息时间"),
    )
    op.add_column(
        "conversations",
        sa.Column("message_count", sa.Integer(), nullable=False,
                  server_default=sa.text("0"), comment="消息条数(不含system)"),
    )
    op.add_column(
        "conversations",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True,
                  comment="软删除时间"),
    )

    # 回填计数和最后可见消息时间。system 提示不算用户可见消息。
    op.execute(
        """
        UPDATE conversations c
        SET last_message_at = m.last_at,
            message_count = m.cnt
        FROM (
            SELECT conversation_id,
                   max(create_time) FILTER (WHERE role <> 'system') AS last_at,
                   count(*) FILTER (WHERE role <> 'system') AS cnt
            FROM messages
            GROUP BY conversation_id
        ) m
        WHERE m.conversation_id = c.id
        """
    )

    op.create_check_constraint(
        "ck_conversations_status", "conversations",
        "status IN ('active', 'archived')",
    )
    op.create_check_constraint(
        "ck_conversations_message_count", "conversations", "message_count >= 0"
    )
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.create_index(
        "ix_conversations_user_update", "conversations",
        ["user_id", "update_time", "id"], unique=False,
    )
    op.create_index(
        "ix_conversations_deleted_at", "conversations", ["deleted_at"], unique=False
    )

    # ------------------------------------------------------------------
    # 4. messages
    # ------------------------------------------------------------------
    op.create_check_constraint(
        "ck_messages_role", "messages",
        "role IN ('user', 'assistant', 'system', 'tool')",
    )
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.create_index(
        "ix_messages_conversation_id_id", "messages",
        ["conversation_id", "id"], unique=False,
    )

    # ------------------------------------------------------------------
    # 5. agent_runs
    # ------------------------------------------------------------------
    # 重命名保留历史 token 数；不能新增 + 删除。
    op.alter_column(
        "agent_runs",
        "tokens",
        new_column_name="total_tokens",
        existing_type=sa.Integer(),
        existing_nullable=False,
    )
    op.add_column(
        "agent_runs",
        sa.Column("user_id", sa.Integer(), nullable=True, comment="所属用户ID"),
    )
    op.add_column(
        "agent_runs",
        sa.Column("request_message_id", sa.Integer(), nullable=True,
                  comment="本轮用户消息ID"),
    )
    op.add_column(
        "agent_runs",
        sa.Column("response_message_id", sa.Integer(), nullable=True,
                  comment="本轮AI回复消息ID"),
    )
    op.add_column(
        "agent_runs",
        sa.Column("trace_id", sa.String(length=64), nullable=True, comment="运行追踪ID"),
    )
    op.add_column(
        "agent_runs",
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True,
                  comment="运行结束时间"),
    )
    op.add_column(
        "agent_runs",
        sa.Column("error_code", sa.String(length=50), nullable=True,
                  comment="错误码/异常类名"),
    )
    op.add_column(
        "agent_runs",
        sa.Column("error_message", sa.String(length=500), nullable=True,
                  comment="对用户安全的错误摘要"),
    )

    op.execute(
        """
        UPDATE agent_runs ar
        SET user_id = c.user_id
        FROM conversations c
        WHERE c.id = ar.conversation_id
        """
    )
    # 只认“确实属于同一会话的 user 消息”，不猜。
    op.execute(
        """
        UPDATE agent_runs ar
        SET request_message_id = (ar.input->>'message_id')::int
        WHERE ar.input ? 'message_id'
          AND ar.input->>'message_id' ~ '^[0-9]+$'
          AND EXISTS (
              SELECT 1 FROM messages m
              WHERE m.id = (ar.input->>'message_id')::int
                AND m.conversation_id = ar.conversation_id
                AND m.role = 'user'
          )
        """
    )
    # request 之后的第一条 assistant 就是这一轮的回复。
    op.execute(
        """
        UPDATE agent_runs ar
        SET response_message_id = (
            SELECT m.id
            FROM messages m
            WHERE m.conversation_id = ar.conversation_id
              AND m.role = 'assistant'
              AND m.id > ar.request_message_id
            ORDER BY m.id
            LIMIT 1
        )
        WHERE ar.request_message_id IS NOT NULL
          AND ar.response_message_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE agent_runs
        SET trace_id = md5('legacy-agent-run:' || id::text)
        WHERE trace_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE agent_runs
        SET finished_at = update_time
        WHERE status <> 'running' AND finished_at IS NULL
        """
    )
    # 历史失败没有保存安全摘要，只写一个可识别的标记；不要回填原始异常文本。
    op.execute(
        """
        UPDATE agent_runs
        SET error_code = 'LEGACY_FAILED',
            error_message = '历史运行失败（原始错误未保存）'
        WHERE status = 'failed' AND error_code IS NULL
        """
    )

    op.alter_column(
        "agent_runs", "user_id",
        existing_type=sa.Integer(), nullable=False,
    )
    op.alter_column(
        "agent_runs", "trace_id",
        existing_type=sa.String(length=64), nullable=False,
    )
    op.alter_column(
        "agent_runs", "status",
        existing_type=sa.String(length=20),
        server_default=sa.text("'running'"), existing_nullable=False,
    )
    op.alter_column(
        "agent_runs", "total_tokens",
        existing_type=sa.Integer(),
        server_default=sa.text("0"), existing_nullable=False,
    )
    op.alter_column(
        "agent_runs", "latency",
        existing_type=sa.Integer(),
        server_default=sa.text("0"), existing_nullable=False,
    )

    op.create_check_constraint(
        "ck_agent_runs_status", "agent_runs",
        "status IN ('running', 'success', 'failed')",
    )
    op.create_check_constraint(
        "ck_agent_runs_total_tokens", "agent_runs", "total_tokens >= 0"
    )
    op.create_check_constraint(
        "ck_agent_runs_latency", "agent_runs", "latency >= 0"
    )
    op.create_unique_constraint(
        "uq_agent_runs_trace_id", "agent_runs", ["trace_id"]
    )
    op.create_foreign_key(
        "fk_agent_runs_user_id", "agent_runs", "users",
        ["user_id"], ["user_id"], ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_agent_runs_request_message", "agent_runs", "messages",
        ["request_message_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_agent_runs_response_message", "agent_runs", "messages",
        ["response_message_id"], ["id"], ondelete="SET NULL",
    )
    op.drop_index("ix_agent_runs_conversation_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_status", table_name="agent_runs")
    op.create_index(
        "ix_agent_runs_conversation_id_id", "agent_runs",
        ["conversation_id", "id"], unique=False,
    )
    op.create_index(
        "ix_agent_runs_user_id_create_time", "agent_runs",
        ["user_id", "create_time"], unique=False,
    )
    op.create_index(
        "ix_agent_runs_status_create_time", "agent_runs",
        ["status", "create_time"], unique=False,
    )

    # ------------------------------------------------------------------
    # 6. documents
    # ------------------------------------------------------------------
    op.add_column(
        "documents",
        sa.Column("file_name", sa.String(length=255), nullable=True,
                  comment="原始文件名"),
    )
    op.add_column(
        "documents",
        sa.Column("content_hash", sa.String(length=64), nullable=True,
                  comment="内容SHA-256"),
    )
    op.add_column(
        "documents",
        sa.Column("source_type", sa.String(length=20), nullable=False,
                  server_default=sa.text("'markdown'"),
                  comment="来源类型: markdown/pdf/url"),
    )
    op.add_column(
        "documents",
        sa.Column("status", sa.String(length=20), nullable=False,
                  server_default=sa.text("'indexed'"),
                  comment="索引状态: pending-待入库, indexed-已入库"),
    )
    op.add_column(
        "documents",
        sa.Column("version", sa.Integer(), nullable=False,
                  server_default=sa.text("1"), comment="内容版本号"),
    )
    op.add_column(
        "documents",
        sa.Column("file_size", sa.Integer(), nullable=True, comment="文件大小(字节)"),
    )
    op.add_column(
        "documents",
        sa.Column("mime_type", sa.String(length=100), nullable=True, comment="MIME类型"),
    )
    op.add_column(
        "documents",
        sa.Column("city", sa.String(length=50), nullable=True, comment="城市"),
    )
    op.add_column(
        "documents",
        sa.Column("doc_type", sa.String(length=30), nullable=True, comment="文档类型"),
    )
    op.add_column(
        "documents",
        sa.Column("language", sa.String(length=10), nullable=False,
                  server_default=sa.text("'zh'"), comment="语言"),
    )
    op.add_column(
        "documents",
        sa.Column("last_indexed_at", sa.DateTime(timezone=True), nullable=True,
                  comment="最近入库时间"),
    )

    op.execute(
        """
        UPDATE documents
        SET file_name = metadata->>'file',
            content_hash = metadata->>'doc_hash',
            city = NULLIF(metadata->>'city', ''),
            doc_type = NULLIF(metadata->>'doc_type', ''),
            status = 'indexed',
            language = 'zh',
            source_type = 'markdown',
            last_indexed_at = update_time,
            mime_type = CASE
                WHEN metadata->>'file' ILIKE '%.md' THEN 'text/markdown'
                ELSE NULL
            END
        """
    )

    op.alter_column(
        "documents", "file_name",
        existing_type=sa.String(length=255), nullable=False,
    )
    op.alter_column(
        "documents", "content_hash",
        existing_type=sa.String(length=64), nullable=False,
    )
    op.alter_column(
        "documents", "chunk_count",
        existing_type=sa.Integer(),
        server_default=sa.text("0"), existing_nullable=False,
    )
    op.create_unique_constraint(
        "uq_documents_file_name", "documents", ["file_name"]
    )
    op.create_check_constraint(
        "ck_documents_status", "documents", "status IN ('pending', 'indexed')"
    )
    op.create_check_constraint(
        "ck_documents_chunk_count", "documents", "chunk_count >= 0"
    )
    op.create_check_constraint(
        "ck_documents_version", "documents", "version > 0"
    )
    op.create_check_constraint(
        "ck_documents_file_size", "documents",
        "file_size IS NULL OR file_size >= 0",
    )
    op.create_index(
        "ix_documents_content_hash", "documents", ["content_hash"], unique=False
    )
    op.create_index("ix_documents_status", "documents", ["status"], unique=False)
    op.create_index("ix_documents_city", "documents", ["city"], unique=False)
    op.create_index("ix_documents_doc_type", "documents", ["doc_type"], unique=False)


def downgrade() -> None:
    """Downgrade schema.

    ⚠️ 这是 schema 回退，不是数据恢复：新列/新表的业务数据会丢失。
    有真实数据要回退时，优先用迁移前的 pg_dump 备份恢复。
    """
    # documents
    op.drop_index("ix_documents_doc_type", table_name="documents")
    op.drop_index("ix_documents_city", table_name="documents")
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_index("ix_documents_content_hash", table_name="documents")
    op.drop_constraint("ck_documents_file_size", "documents", type_="check")
    op.drop_constraint("ck_documents_version", "documents", type_="check")
    op.drop_constraint("ck_documents_chunk_count", "documents", type_="check")
    op.drop_constraint("ck_documents_status", "documents", type_="check")
    op.drop_constraint("uq_documents_file_name", "documents", type_="unique")
    op.alter_column(
        "documents", "chunk_count",
        existing_type=sa.Integer(), server_default=None, existing_nullable=False,
    )
    op.drop_column("documents", "last_indexed_at")
    op.drop_column("documents", "language")
    op.drop_column("documents", "doc_type")
    op.drop_column("documents", "city")
    op.drop_column("documents", "mime_type")
    op.drop_column("documents", "file_size")
    op.drop_column("documents", "version")
    op.drop_column("documents", "status")
    op.drop_column("documents", "source_type")
    op.drop_column("documents", "content_hash")
    op.drop_column("documents", "file_name")

    # agent_runs
    op.drop_index("ix_agent_runs_status_create_time", table_name="agent_runs")
    op.drop_index("ix_agent_runs_user_id_create_time", table_name="agent_runs")
    op.drop_index("ix_agent_runs_conversation_id_id", table_name="agent_runs")
    op.drop_constraint("fk_agent_runs_response_message", "agent_runs", type_="foreignkey")
    op.drop_constraint("fk_agent_runs_request_message", "agent_runs", type_="foreignkey")
    op.drop_constraint("fk_agent_runs_user_id", "agent_runs", type_="foreignkey")
    op.drop_constraint("uq_agent_runs_trace_id", "agent_runs", type_="unique")
    op.drop_constraint("ck_agent_runs_latency", "agent_runs", type_="check")
    op.drop_constraint("ck_agent_runs_total_tokens", "agent_runs", type_="check")
    op.drop_constraint("ck_agent_runs_status", "agent_runs", type_="check")
    op.alter_column(
        "agent_runs", "latency",
        existing_type=sa.Integer(), server_default=None, existing_nullable=False,
    )
    op.alter_column(
        "agent_runs", "total_tokens",
        existing_type=sa.Integer(), server_default=None, existing_nullable=False,
    )
    op.alter_column(
        "agent_runs", "status",
        existing_type=sa.String(length=20), server_default=None, existing_nullable=False,
    )
    op.alter_column(
        "agent_runs", "total_tokens",
        new_column_name="tokens",
        existing_type=sa.Integer(), existing_nullable=False,
    )
    op.drop_column("agent_runs", "error_message")
    op.drop_column("agent_runs", "error_code")
    op.drop_column("agent_runs", "finished_at")
    op.drop_column("agent_runs", "trace_id")
    op.drop_column("agent_runs", "response_message_id")
    op.drop_column("agent_runs", "request_message_id")
    op.drop_column("agent_runs", "user_id")
    op.create_index(
        "ix_agent_runs_status", "agent_runs", ["status"], unique=False
    )
    op.create_index(
        "ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"], unique=False
    )

    # messages
    op.drop_index("ix_messages_conversation_id_id", table_name="messages")
    op.drop_constraint("ck_messages_role", "messages", type_="check")
    op.create_index(
        "ix_messages_conversation_id", "messages", ["conversation_id"], unique=False
    )

    # conversations
    op.drop_index("ix_conversations_deleted_at", table_name="conversations")
    op.drop_index("ix_conversations_user_update", table_name="conversations")
    op.drop_constraint(
        "ck_conversations_message_count", "conversations", type_="check"
    )
    op.drop_constraint("ck_conversations_status", "conversations", type_="check")
    op.drop_column("conversations", "deleted_at")
    op.drop_column("conversations", "message_count")
    op.drop_column("conversations", "last_message_at")
    op.drop_column("conversations", "status")
    op.create_index(
        "ix_conversations_user_id", "conversations", ["user_id"], unique=False
    )

    # users
    op.drop_constraint("uq_users_phone", "users", type_="unique")
    op.drop_constraint("uq_users_email", "users", type_="unique")
    op.drop_constraint("ck_users_status", "users", type_="check")
    op.drop_constraint("ck_users_role", "users", type_="check")
    # 先删默认值：'active' 不能当 integer 的默认值，必须和类型转换分开做。
    op.alter_column(
        "users", "status",
        existing_type=sa.String(length=20),
        server_default=None,
        existing_nullable=False,
    )
    op.alter_column(
        "users", "status",
        existing_type=sa.String(length=20), type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using=(
            "CASE status WHEN 'active' THEN 1 WHEN 'disabled' THEN 0 ELSE 0 END"
        ),
    )
    op.alter_column(
        "users", "role",
        existing_type=sa.String(length=20), type_=sa.String(length=50),
        server_default=None, existing_nullable=False,
    )
    op.alter_column(
        "users", "avatar",
        existing_type=sa.String(length=500), type_=sa.String(length=50),
        existing_nullable=True,
    )
    op.alter_column(
        "users", "phone",
        existing_type=sa.String(length=32), type_=sa.String(length=50),
        existing_nullable=True,
    )
    op.alter_column(
        "users", "email",
        existing_type=sa.String(length=255), type_=sa.String(length=50),
        existing_nullable=True,
    )
    op.drop_column("users", "last_login_at")
    op.alter_column(
        "users", "password_hash",
        new_column_name="password",
        existing_type=sa.String(length=255), existing_nullable=False,
    )

    # 时间戳最后统一转回 naive（downgrade 顺序与 upgrade 相反）
    for table in reversed(_TABLES):
        _to_naive_timestamp(table)
