"""Drop studies.match_key and identify a study by section and title.

Revision ID: 20261116_0001
Revises: 20261111_0001
Create Date: 2026-11-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261116_0001"
down_revision: str | None = "20261111_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("studies_match_key_key", "studies", type_="unique")
    op.drop_column("studies", "match_key")
    op.create_unique_constraint("studies_section_title_key", "studies", ["section", "title"])


def downgrade() -> None:
    op.drop_constraint("studies_section_title_key", "studies", type_="unique")
    op.add_column("studies", sa.Column("match_key", sa.String(length=800), nullable=True))
    op.execute("UPDATE studies SET match_key = section || chr(10) || title")
    op.alter_column("studies", "match_key", nullable=False)
    op.create_unique_constraint("studies_match_key_key", "studies", ["match_key"])
