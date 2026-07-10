"""add_build_telemetry

Adds build_sessions and module_decisions tables for DSPy recommender telemetry
(the future GEPA training dataset).

Revision ID: e1a2b3c4d5e6
Revises: b954e7813984
Create Date: 2026-07-10

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'e1a2b3c4d5e6'
down_revision = 'b954e7813984'
branch_labels = None
depends_on = None


def upgrade() -> None:
    sa.Enum(
        'running', 'completed', 'abandoned', 'error',
        name='build_session_status',
    ).create(op.get_bind())

    op.create_table(
        'build_sessions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('pipeline_version', sa.Text(), nullable=False),
        sa.Column('budget_cents', sa.Integer(), nullable=True),
        sa.Column('price_sensitivity', sa.Text(), nullable=True),
        sa.Column('input_profile', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('final_build', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('total_cost_usd', sa.Numeric(), nullable=True),
        sa.Column('total_latency_ms', sa.Integer(), nullable=True),
        sa.Column('budget_delta_cents', sa.Integer(), nullable=True),
        sa.Column('compatibility_override_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column(
            'status',
            postgresql.ENUM(
                'running', 'completed', 'abandoned', 'error',
                name='build_session_status', create_type=False,
            ),
            server_default='running',
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_build_sessions_id'), 'build_sessions', ['id'], unique=False)
    op.create_index(op.f('ix_build_sessions_user_id'), 'build_sessions', ['user_id'], unique=False)

    op.create_table(
        'module_decisions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('session_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('pipeline_version', sa.Text(), nullable=False),
        sa.Column('category', sa.String(length=32), nullable=False),
        sa.Column('sequence_order', sa.Integer(), nullable=False),
        sa.Column('signature_name', sa.String(length=64), nullable=False),
        sa.Column('signature_version', sa.Integer(), nullable=False),
        sa.Column('candidate_set', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('input_state', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('raw_prompt_hash', sa.String(length=64), nullable=True),
        sa.Column('output_decision', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('tokens_in', sa.Integer(), nullable=True),
        sa.Column('tokens_out', sa.Integer(), nullable=True),
        sa.Column('cost_usd', sa.Numeric(), nullable=True),
        sa.Column('latency_ms', sa.Integer(), nullable=True),
        sa.Column('was_override', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('model_name', sa.String(length=128), nullable=True),
        sa.Column('outcome_signal', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['build_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_module_decisions_id'), 'module_decisions', ['id'], unique=False)
    op.create_index(op.f('ix_module_decisions_session_id'), 'module_decisions', ['session_id'], unique=False)
    op.create_index(
        'ix_module_decisions_category_pipeline_created',
        'module_decisions',
        ['category', 'pipeline_version', 'created_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_module_decisions_category_pipeline_created', table_name='module_decisions')
    op.drop_index(op.f('ix_module_decisions_session_id'), table_name='module_decisions')
    op.drop_index(op.f('ix_module_decisions_id'), table_name='module_decisions')
    op.drop_table('module_decisions')

    op.drop_index(op.f('ix_build_sessions_user_id'), table_name='build_sessions')
    op.drop_index(op.f('ix_build_sessions_id'), table_name='build_sessions')
    op.drop_table('build_sessions')

    sa.Enum(name='build_session_status').drop(op.get_bind())
