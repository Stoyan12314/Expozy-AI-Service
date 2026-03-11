"""Add phone column to telegram_login_state

Revision ID: 003_add_phone_to_login_state
Revises: 002_telegram_sessions
Create Date: 2026-03-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '003_add_phone_to_login_state'
down_revision: Union[str, None] = '002_telegram_sessions'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'telegram_login_state',
        sa.Column('phone', sa.String(length=50), nullable=True,
                  comment='Collected phone number during newstore flow'),
    )


def downgrade() -> None:
    op.drop_column('telegram_login_state', 'phone')