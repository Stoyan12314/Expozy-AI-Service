"""Refactor telegram_sessions to support multiple stores per user

Revision ID: 004_multistore_sessions
Revises: 003_add_phone_to_login_state
Create Date: 2026-03-06

Changes:
- Drop unique constraint on telegram_id alone
- Add auto-increment id as primary key
- Add is_active boolean column
- Add unique constraint on (telegram_id, project)
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '004_multistore_sessions'
down_revision: Union[str, None] = '003_add_phone_to_login_state'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_exists(conn, index_name: str) -> bool:
    result = conn.execute(sa.text(
        "SELECT 1 FROM pg_indexes WHERE indexname = :name"
    ), {"name": index_name})
    return result.fetchone() is not None


def _constraint_exists(conn, constraint_name: str) -> bool:
    result = conn.execute(sa.text(
        "SELECT 1 FROM pg_constraint WHERE conname = :name"
    ), {"name": constraint_name})
    return result.fetchone() is not None


def _column_exists(conn, table: str, column: str) -> bool:
    result = conn.execute(sa.text(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name = :table AND column_name = :column"
    ), {"table": table, "column": column})
    return result.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Drop the old unique index on telegram_id (if exists)
    if _index_exists(conn, 'ix_telegram_sessions_telegram_id'):
        op.drop_index('ix_telegram_sessions_telegram_id', table_name='telegram_sessions')

    if _constraint_exists(conn, 'uq_telegram_sessions_telegram_id'):
        op.drop_constraint('uq_telegram_sessions_telegram_id', 'telegram_sessions', type_='unique')

    # 2. Drop old PK on telegram_id (if it's still the PK)
    if _constraint_exists(conn, 'telegram_sessions_pkey'):
        op.drop_constraint('telegram_sessions_pkey', 'telegram_sessions', type_='primary')

    # 3. Add id column only if it doesn't already exist
    if not _column_exists(conn, 'telegram_sessions', 'id'):
        op.add_column(
            'telegram_sessions',
            sa.Column('id', sa.BigInteger(), nullable=False, autoincrement=True)
        )

    op.execute("CREATE SEQUENCE IF NOT EXISTS telegram_sessions_id_seq")
    op.execute("ALTER TABLE telegram_sessions ALTER COLUMN id SET DEFAULT nextval('telegram_sessions_id_seq')")
    op.execute("""
        SELECT setval('telegram_sessions_id_seq', COALESCE(MAX(id), 0) + 1)
        FROM telegram_sessions
    """)
    op.execute("UPDATE telegram_sessions SET id = nextval('telegram_sessions_id_seq') WHERE id = 0 OR id IS NULL")

    # Re-create PK on id (only if not already set)
    if not _constraint_exists(conn, 'telegram_sessions_pkey'):
        op.create_primary_key('telegram_sessions_pkey', 'telegram_sessions', ['id'])

    # 4. Add is_active column if not exists
    if not _column_exists(conn, 'telegram_sessions', 'is_active'):
        op.add_column(
            'telegram_sessions',
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default='false')
        )
    op.execute("UPDATE telegram_sessions SET is_active = true")

    # 5. Add index on telegram_id
    if not _index_exists(conn, 'ix_telegram_sessions_telegram_id'):
        op.create_index('ix_telegram_sessions_telegram_id', 'telegram_sessions', ['telegram_id'])

    # 6. Add unique constraint on (telegram_id, project)
    if not _constraint_exists(conn, 'uq_telegram_sessions_user_project'):
        op.create_unique_constraint(
            'uq_telegram_sessions_user_project',
            'telegram_sessions',
            ['telegram_id', 'project'],
        )


def downgrade() -> None:
    conn = op.get_bind()

    if _constraint_exists(conn, 'uq_telegram_sessions_user_project'):
        op.drop_constraint('uq_telegram_sessions_user_project', 'telegram_sessions', type_='unique')

    if _index_exists(conn, 'ix_telegram_sessions_telegram_id'):
        op.drop_index('ix_telegram_sessions_telegram_id', table_name='telegram_sessions')

    if _column_exists(conn, 'telegram_sessions', 'is_active'):
        op.drop_column('telegram_sessions', 'is_active')

    if _constraint_exists(conn, 'telegram_sessions_pkey'):
        op.drop_constraint('telegram_sessions_pkey', 'telegram_sessions', type_='primary')

    if _column_exists(conn, 'telegram_sessions', 'id'):
        op.drop_column('telegram_sessions', 'id')

    op.create_primary_key('telegram_sessions_pkey', 'telegram_sessions', ['telegram_id'])

    if not _constraint_exists(conn, 'uq_telegram_sessions_telegram_id'):
        op.create_unique_constraint('uq_telegram_sessions_telegram_id', 'telegram_sessions', ['telegram_id'])

    if not _index_exists(conn, 'ix_telegram_sessions_telegram_id'):
        op.create_index('ix_telegram_sessions_telegram_id', 'telegram_sessions', ['telegram_id'], unique=True)