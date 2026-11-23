"""Drop pitches.status and rename prospect_text to title.

Revision ID: 20261122_0001
Revises: 20261116_0001
Create Date: 2026-11-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261122_0001"
down_revision: str | None = "20261116_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("pitches", "status")
    op.execute("ALTER TABLE pitches RENAME COLUMN prospect_text TO title")
    op.execute(
        "UPDATE pitches SET title = left(title, 200) WHERE char_length(title) > 200"
    )
    op.alter_column(
        "pitches",
        "title",
        existing_type=sa.Text(),
        type_=sa.String(length=200),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "pitches",
        "title",
        existing_type=sa.String(length=200),
        type_=sa.Text(),
        existing_nullable=False,
    )
    op.execute("ALTER TABLE pitches RENAME COLUMN title TO prospect_text")
    op.add_column(
        "pitches",
        sa.Column("status", sa.String(length=32), nullable=False, server_default="collecting"),
    )
    op.alter_column("pitches", "status", server_default=None)
