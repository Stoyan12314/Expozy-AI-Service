"""Initial schema with telegram_update, job, and job_attempt tables

Revision ID: 001_initial_schema
Revises: 
Create Date: 2024-01-15

Tables:
- telegram_update: Idempotency tracking for webhook deduplication
- job: Main job tracking with status lifecycle
- job_attempt: Detailed attempt history for debugging

Indexes:
- telegram_update(update_id) - UNIQUE for idempotency
- job(status, created_at) - Queue processing queries
- job(bundle_id) - UNIQUE nullable for bundle lookup
- job_attempt(job_id, attempt_no) - UNIQUE for attempt tracking
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create ENUM types first using raw SQL - idempotent with DO block
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE job_status AS ENUM ('queued', 'running', 'completed', 'failed');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE attempt_outcome AS ENUM ('success', 'fail');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)
    
    # Reference existing enum types (don't create them again)
    job_status_enum = postgresql.ENUM(
        'queued', 'running', 'completed', 'failed',
        name='job_status',
        create_type=False  # Already created above
    )
    
    attempt_outcome_enum = postgresql.ENUM(
        'success', 'fail',
        name='attempt_outcome',
        create_type=False  # Already created above
    )
    # =========================================================================
    # telegram_update table
    # =========================================================================
    op.create_table(
        'telegram_update',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False,
                  comment='Internal primary key'),
        sa.Column('update_id', sa.BigInteger(), nullable=False,
                  comment='Telegram update_id - unique per bot, used for idempotency'),
        sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False, comment='Timestamp when webhook was received'),
        sa.Column('raw_update', postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                  comment='Complete raw Telegram update JSON payload'),
        sa.PrimaryKeyConstraint('id'),
        comment='Telegram webhook updates for idempotency tracking'
    )
    
    # Indexes for telegram_update
    op.create_index('ix_telegram_update_update_id', 'telegram_update', ['update_id'], unique=True)
    op.create_index('ix_telegram_update_received_at', 'telegram_update', ['received_at'])
    
    # Explicit unique constraint (creates index automatically but we want named constraint)
    op.create_unique_constraint('uq_telegram_update_update_id', 'telegram_update', ['update_id'])
    
    # =========================================================================
    # job table
    # =========================================================================
    op.create_table(
        'job',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False,
                  comment='Unique job identifier (UUID v4)'),
        sa.Column('telegram_update_id', sa.BigInteger(), nullable=True,
                  comment='Reference to source Telegram update'),
        sa.Column('chat_id', sa.BigInteger(), nullable=False,
                  comment='Telegram chat ID for sending responses'),
        sa.Column('user_id', sa.BigInteger(), nullable=True,
                  comment='Telegram user ID who initiated the request'),
        sa.Column('prompt_text', sa.Text(), nullable=False,
                  comment="User's prompt text for AI generation"),
        sa.Column('status', job_status_enum, nullable=False, server_default='queued',
                  comment='Current job status in lifecycle'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False, comment='Job creation timestamp'),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False, comment='Last update timestamp'),
        sa.Column('bundle_id', postgresql.UUID(as_uuid=True), nullable=True,
                  comment='Generated bundle UUID (unique, nullable)'),
        sa.Column('preview_url', sa.String(length=500), nullable=True,
                  comment='Public preview URL for the generated page'),
        sa.Column('error_message', sa.Text(), nullable=True,
                  comment='Human-readable error message on failure'),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0',
                  comment='Number of processing attempts made'),
        sa.Column('raw_ai_response', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='Raw AI provider response JSON for auditing'),
        sa.Column('validation_errors', postgresql.JSONB(astext_type=sa.Text()), nullable=True,
                  comment='Template validation errors if any'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['telegram_update_id'], ['telegram_update.id'],
                                ondelete='SET NULL', name='fk_job_telegram_update'),
        sa.CheckConstraint('attempt_count >= 0', name='ck_job_attempt_count_positive'),
        comment='AI generation jobs with status tracking'
    )
    
    # Indexes for job
    op.create_index('ix_job_status', 'job', ['status'])
    op.create_index('ix_job_chat_id', 'job', ['chat_id'])
    op.create_index('ix_job_telegram_update_id', 'job', ['telegram_update_id'])
    
    # Composite indexes for common queries
    op.create_index('ix_job_status_created_at', 'job', ['status', 'created_at'])
    op.create_index('ix_job_chat_id_created_at', 'job', ['chat_id', 'created_at'])
    
    # Unique constraint on bundle_id (nullable unique - PostgreSQL allows multiple NULLs)
    op.create_unique_constraint('uq_job_bundle_id', 'job', ['bundle_id'])
    
    # =========================================================================
    # job_attempt table
    # =========================================================================
    op.create_table(
        'job_attempt',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('job_id', postgresql.UUID(as_uuid=True), nullable=False,
                  comment='Reference to parent job'),
        sa.Column('attempt_no', sa.SmallInteger(), nullable=False,
                  comment='Attempt number (1-indexed)'),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                  nullable=False, comment='When this attempt started'),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True,
                  comment='When this attempt finished (null if still running)'),
        sa.Column('outcome', attempt_outcome_enum, nullable=True,
                  comment='Attempt outcome (null while running)'),
        sa.Column('error_detail', sa.Text(), nullable=True,
                  comment='Detailed error message if failed'),
        sa.Column('provider_status_code', sa.SmallInteger(), nullable=True,
                  comment='HTTP status code from AI provider (429 = rate limited, etc.)'),
        sa.Column('provider_name', sa.String(length=50), nullable=True,
                  comment='AI provider used for this attempt'),
        sa.Column('duration_ms', sa.Integer(), nullable=True,
                  comment='Attempt duration in milliseconds'),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['job_id'], ['job.id'],
                                ondelete='CASCADE', name='fk_job_attempt_job'),
        sa.CheckConstraint('attempt_no > 0', name='ck_job_attempt_no_positive'),
        sa.CheckConstraint('duration_ms IS NULL OR duration_ms >= 0',
                           name='ck_job_attempt_duration_positive'),
        comment='Individual job processing attempts for debugging'
    )
    
    # Indexes for job_attempt
    op.create_index('ix_job_attempt_job_id', 'job_attempt', ['job_id'])
    op.create_index('ix_job_attempt_started_at', 'job_attempt', ['started_at'])
    op.create_index('ix_job_attempt_outcome_provider', 'job_attempt',
                ['outcome', 'provider_status_code'])
    
    # Unique constraint: one attempt number per job
    op.create_unique_constraint('uq_job_attempt_job_attempt_no', 'job_attempt',
                                ['job_id', 'attempt_no'])
    
    # =========================================================================
    # Create trigger for updated_at auto-update
    # =========================================================================
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ language 'plpgsql';
    """)
    
    op.execute("""
        CREATE TRIGGER update_job_updated_at
        BEFORE UPDATE ON job
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
    """)


def downgrade() -> None:
    # Drop trigger and function
    op.execute("DROP TRIGGER IF EXISTS update_job_updated_at ON job")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at_column()")
    
    # Drop job_attempt table
    op.drop_constraint('uq_job_attempt_job_attempt_no', 'job_attempt', type_='unique')
    op.drop_index('ix_job_attempt_outcome_provider', table_name='job_attempt')
    op.drop_index('ix_job_attempt_started_at', table_name='job_attempt')
    op.drop_index('ix_job_attempt_job_id', table_name='job_attempt')
    op.drop_table('job_attempt')
    
    # Drop job table
    op.drop_constraint('uq_job_bundle_id', 'job', type_='unique')
    op.drop_index('ix_job_chat_id_created_at', table_name='job')
    op.drop_index('ix_job_status_created_at', table_name='job')
    op.drop_index('ix_job_telegram_update_id', table_name='job')
    op.drop_index('ix_job_chat_id', table_name='job')
    op.drop_index('ix_job_status', table_name='job')
    op.drop_table('job')
    
    # Drop telegram_update table
    op.drop_constraint('uq_telegram_update_update_id', 'telegram_update', type_='unique')
    op.drop_index('ix_telegram_update_received_at', table_name='telegram_update')
    op.drop_index('ix_telegram_update_update_id', table_name='telegram_update')
    op.drop_table('telegram_update')
    
    # Drop ENUM types
    op.execute("DROP TYPE IF EXISTS attempt_outcome")
    op.execute("DROP TYPE IF EXISTS job_status")
