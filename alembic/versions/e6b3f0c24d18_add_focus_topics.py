"""add focus_topics to videos

A submission may ask for notes on specific topics instead of the whole video.
The topics are kept on the record - not only handed to the model - so the
finished notes can show what they were narrowed to, and so a reprocess uses the
same filter. Null means "the whole video", which is what every existing row is.

Revision ID: e6b3f0c24d18
Revises: d4f8a1b6c907
Create Date: 2026-09-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6b3f0c24d18'
down_revision: Union[str, None] = 'd4f8a1b6c907'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'videos',
        sa.Column('focus_topics', sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('videos', 'focus_topics')
