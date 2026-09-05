"""rename_clerk_id_to_google_id

Revision ID: f5ffaae4112a
Revises: 0001
Create Date: 2026-07-18 08:45:08.203517

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f5ffaae4112a'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('users', 'clerk_id', new_column_name='google_id')


def downgrade() -> None:
    op.alter_column('users', 'google_id', new_column_name='clerk_id')
