"""agent run spans

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-27 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'agent_run_spans',
        sa.Column(
            'id', sa.Integer(), autoincrement=True, nullable=False, comment='主键ID'
        ),
        sa.Column(
            'run_id', sa.Integer(), nullable=False, comment='所属运行ID'
        ),
        sa.Column(
            'trace_id', sa.String(length=64), nullable=False, comment='追踪ID'
        ),
        sa.Column(
            'span_id', sa.String(length=32), nullable=False, comment='Span ID'
        ),
        sa.Column(
            'parent_span_id', sa.String(length=32), nullable=True, comment='父Span ID'
        ),
        sa.Column(
            'name', sa.String(length=100), nullable=False, comment='Span名称'
        ),
        sa.Column(
            'span_type',
            sa.String(length=20),
            nullable=False,
            comment='类型: run/graph/node/llm/mcp/db/cache/rag/http',
        ),
        sa.Column(
            'node_name', sa.String(length=50), nullable=True, comment='节点名'
        ),
        sa.Column(
            'status',
            sa.String(length=20),
            server_default='running',
            nullable=False,
            comment='状态: running/success/failed/skipped',
        ),
        sa.Column(
            'start_time',
            sa.DateTime(timezone=True),
            nullable=False,
            comment='开始时间',
        ),
        sa.Column(
            'end_time',
            sa.DateTime(timezone=True),
            nullable=True,
            comment='结束时间',
        ),
        sa.Column(
            'duration_ms',
            sa.Integer(),
            server_default='0',
            nullable=False,
            comment='耗时(毫秒)',
        ),
        sa.Column(
            'tokens',
            sa.Integer(),
            server_default='0',
            nullable=False,
            comment='token用量',
        ),
        sa.Column(
            'error_code', sa.String(length=50), nullable=True, comment='错误码'
        ),
        sa.Column(
            'error_message',
            sa.String(length=500),
            nullable=True,
            comment='安全错误摘要',
        ),
        sa.Column(
            'attributes',
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment='附加属性(JSON)',
        ),
        sa.Column(
            'create_time',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
            comment='创建时间',
        ),
        sa.Column(
            'update_time',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
            comment='更新时间',
        ),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed', 'skipped')",
            name='ck_agent_run_spans_status',
        ),
        sa.CheckConstraint('duration_ms >= 0', name='ck_agent_run_spans_duration'),
        sa.CheckConstraint('tokens >= 0', name='ck_agent_run_spans_tokens'),
        sa.ForeignKeyConstraint(['run_id'], ['agent_runs.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'run_id', 'span_id', name='uq_agent_run_spans_run_span'
        ),
        comment='智能体运行 span 明细表',
    )
    op.create_index(
        'ix_agent_run_spans_create_time',
        'agent_run_spans',
        ['create_time'],
        unique=False,
    )
    op.create_index(
        'ix_agent_run_spans_node_status',
        'agent_run_spans',
        ['node_name', 'status'],
        unique=False,
    )
    op.create_index(
        'ix_agent_run_spans_run_start',
        'agent_run_spans',
        ['run_id', 'start_time'],
        unique=False,
    )
    op.create_index(
        'ix_agent_run_spans_trace_start',
        'agent_run_spans',
        ['trace_id', 'start_time'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        'ix_agent_run_spans_trace_start', table_name='agent_run_spans'
    )
    op.drop_index(
        'ix_agent_run_spans_run_start', table_name='agent_run_spans'
    )
    op.drop_index(
        'ix_agent_run_spans_node_status', table_name='agent_run_spans'
    )
    op.drop_index(
        'ix_agent_run_spans_create_time', table_name='agent_run_spans'
    )
    op.drop_table('agent_run_spans')
