"""pitch_pages.body replaces section fields; drop pitch_pdfs.

Revision ID: 20261125_0001
Revises: 20261124_0001
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261125_0001"
down_revision: str | None = "20261124_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("pitch_pdfs")
    op.add_column("pitch_pages", sa.Column("body", sa.Text(), nullable=False, server_default=""))
    op.drop_column("pitch_pages", "background")
    op.drop_column("pitch_pages", "challenges")
    op.drop_column("pitch_pages", "solution")
    op.drop_column("pitch_pages", "key_highlights")
    op.drop_column("pitch_pages", "impact")
    op.drop_column("pitch_pages", "technology")
    op.alter_column("pitch_pages", "body", server_default=None)


def downgrade() -> None:
    op.add_column(
        "pitch_pages",
        sa.Column("technology", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "pitch_pages",
        sa.Column("impact", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "pitch_pages",
        sa.Column("key_highlights", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "pitch_pages",
        sa.Column("solution", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "pitch_pages",
        sa.Column("challenges", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "pitch_pages",
        sa.Column("background", sa.Text(), nullable=False, server_default=""),
    )
    op.drop_column("pitch_pages", "body")
    for column in (
        "technology",
        "impact",
        "key_highlights",
        "solution",
        "challenges",
        "background",
    ):
        op.alter_column("pitch_pages", column, server_default=None)
    op.create_table(
        "pitch_pdfs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["pitch_revisions.id"],
            name="pitch_pdfs_revision_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="pitch_pdfs_pkey"),
    )
