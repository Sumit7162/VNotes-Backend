"""add video source and allow null youtube_url

Records created from an uploaded transcript have no YouTube URL, so the column
loses its NOT NULL constraint and a `source` column records which of the two
entry points a row came from. Existing rows are all YouTube submissions, which
is what the server default backfills them with.

Revision ID: a1c7d2e93f40
Revises: f5ffaae4112a
Create Date: 2026-09-09 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c7d2e93f40'
down_revision: Union[str, None] = 'f5ffaae4112a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'videos',
        sa.Column('source', sa.String(length=20), nullable=False, server_default='youtube'),
    )
    op.alter_column('videos', 'youtube_url', existing_type=sa.String(length=500), nullable=True)


def downgrade() -> None:
    # Transcript uploads have no URL to restore, so they are dropped rather
    # than blocking the column going back to NOT NULL.
    op.execute("DELETE FROM videos WHERE youtube_url IS NULL")
    op.alter_column('videos', 'youtube_url', existing_type=sa.String(length=500), nullable=False)
    op.drop_column('videos', 'source')
