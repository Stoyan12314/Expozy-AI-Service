"""Add telegram_sessions and telegram_login_state tables

Revision ID: 002_telegram_sessions
Revises: 001_initial_schema
Create Date: 2026-03-04

Tables:
- telegram_sessions: Stores Expozy login tokens per Telegram user
- telegram_login_state: Tracks multi-step login conversation state
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision: str = '002_telegram_sessions'
down_revision: Union[str, None] = '001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:

    # =========================================================================
    # telegram_sessions table
    # Stores Expozy project credentials per Telegram user
    # =========================================================================
    op.create_table(
        'telegram_sessions',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False,
                  comment='Telegram user ID'),
        sa.Column('project', sa.String(length=255), nullable=False,
                  comment='Expozy project name e.g. mystore'),
        sa.Column('token', sa.Text(), nullable=False,
                  comment='Bearer token from login_telegram'),
        sa.Column('saas_key', sa.String(length=255), nullable=False,
                  comment='SaaS key from saas_telegram registration'),
        sa.Column('project_url', sa.String(length=255), nullable=False,
                  comment='Full project URL e.g. https://mystore.expozy.net'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('telegram_id', name='uq_telegram_sessions_telegram_id'),
        comment='Expozy login sessions per Telegram user'
    )

    op.create_index('ix_telegram_sessions_telegram_id', 'telegram_sessions', ['telegram_id'], unique=True)

    # =========================================================================
    # telegram_login_state table
    # Tracks where a user is in the multi-step /login or /newstore conversation
    # =========================================================================
    op.create_table(
        'telegram_login_state',
        sa.Column('telegram_id', sa.BigInteger(), nullable=False,
                  comment='Telegram user ID (primary key)'),
        sa.Column('step', sa.String(length=64), nullable=False,
                  server_default='idle',
                  comment='Current step: idle | newstore:title | newstore:phone | newstore:email | newstore:password | login:email | login:password'),
        sa.Column('project', sa.String(length=255), nullable=True,
                  comment='Collected project/store name during flow'),
        sa.Column('email', sa.String(length=255), nullable=True,
                  comment='Collected email during flow'),
        sa.Column('phone', sa.String(length=50), nullable=True,
                  comment='Collected phone number during newstore flow'),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('telegram_id'),
        comment='Multi-step conversation state per Telegram user'
    )

    # Auto-update updated_at on telegram_sessions (reuse existing function from 001)
    op.execute("""
        CREATE TRIGGER update_telegram_sessions_updated_at
        BEFORE UPDATE ON telegram_sessions
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
    """)

    op.execute("""
        CREATE TRIGGER update_telegram_login_state_updated_at
        BEFORE UPDATE ON telegram_login_state
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
    """)


def downgrade() -> None:
    # Drop triggers
    op.execute("DROP TRIGGER IF EXISTS update_telegram_sessions_updated_at ON telegram_sessions")
    op.execute("DROP TRIGGER IF EXISTS update_telegram_login_state_updated_at ON telegram_login_state")

    # Drop tables
    op.drop_index('ix_telegram_sessions_telegram_id', table_name='telegram_sessions')
    op.drop_constraint('uq_telegram_sessions_telegram_id', 'telegram_sessions', type_='unique')
    op.drop_table('telegram_login_state')
    op.drop_table('telegram_sessions')