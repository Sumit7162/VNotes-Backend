"""add password sign-in columns to users

Adds the two columns an email-and-password account needs, and makes google_id
nullable because such an account has no Google identity at all.

Every row that exists when this runs was created by Google sign-in, so they are
backfilled as verified - Google had already proved those addresses, and marking
them unverified would lock out every existing user.

Revision ID: d4f8a1b6c907
Revises: c3e91b7d4a52
Create Date: 2026-09-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4f8a1b6c907'
down_revision: Union[str, None] = 'c3e91b7d4a52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('users', sa.Column('password_hash', sa.String(length=255), nullable=True))
    op.add_column(
        'users',
        sa.Column(
            'email_verified',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    # Existing accounts all came in through Google, which verifies the address.
    op.execute("UPDATE users SET email_verified = true WHERE google_id IS NOT NULL")

    op.alter_column('users', 'google_id', existing_type=sa.String(length=255), nullable=True)


def downgrade() -> None:
    # Password-only accounts have no google_id, so they cannot survive the
    # column going back to NOT NULL. They are removed rather than left to break
    # the constraint; there is nowhere else to put them.
    op.execute("DELETE FROM users WHERE google_id IS NULL")
    op.alter_column('users', 'google_id', existing_type=sa.String(length=255), nullable=False)
    op.drop_column('users', 'email_verified')
    op.drop_column('users', 'password_hash')
