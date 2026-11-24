"""Add study_chunks.text_search tsvector and GIN index.

Revision ID: 20261124_0001
Revises: 20261122_0001
Create Date: 2026-11-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261124_0001"
down_revision: str | None = "20261122_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE study_chunks
        ADD COLUMN text_search tsvector
        GENERATED ALWAYS AS (to_tsvector('english', coalesce(text, ''))) STORED
        """
    )
    op.execute(
        """
        CREATE INDEX study_chunks_text_search_idx
        ON study_chunks USING GIN (text_search)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS study_chunks_text_search_idx")
    op.execute("ALTER TABLE study_chunks DROP COLUMN IF EXISTS text_search")
