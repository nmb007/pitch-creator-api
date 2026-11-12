"""Create the pitch schema, including tables Week 2 will fill.

Revision ID: 20261111_0001
Revises:
Create Date: 2026-11-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "20261111_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("username", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slack_user_id", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id", name="users_pkey"),
        sa.UniqueConstraint("username", name="users_username_key"),
        sa.UniqueConstraint("slack_user_id", name="users_slack_user_id_key"),
    )
    op.create_index("users_username_idx", "users", ["username"])
    op.create_table(
        "deck_imports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column("file_name", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], name="deck_imports_uploaded_by_fkey"),
        sa.PrimaryKeyConstraint("id", name="deck_imports_pkey"),
    )
    op.create_table(
        "studies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("section", sa.String(length=300), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("client_name", sa.String(length=300), nullable=True),
        sa.Column("match_key", sa.String(length=800), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id", name="studies_pkey"),
        sa.UniqueConstraint("match_key", name="studies_match_key_key"),
    )
    op.create_table(
        "study_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("study_id", sa.Uuid(), nullable=False),
        sa.Column("deck_import_id", sa.Uuid(), nullable=False),
        sa.Column("slide_numbers", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("background", sa.Text(), nullable=False),
        sa.Column("challenge", sa.Text(), nullable=False),
        sa.Column("approach", sa.Text(), nullable=False),
        sa.Column("solution", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("tech_stack", sa.Text(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("summary_embedding", Vector(384), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["deck_import_id"], ["deck_imports.id"], name="study_versions_deck_import_id_fkey"),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"], name="study_versions_study_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="study_versions_pkey"),
    )
    op.create_table(
        "study_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("study_version_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.ForeignKeyConstraint(
            ["study_version_id"],
            ["study_versions.id"],
            name="study_chunks_study_version_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="study_chunks_pkey"),
    )
    op.create_table(
        "study_images",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("study_version_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["study_version_id"],
            ["study_versions.id"],
            name="study_images_study_version_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="study_images_pkey"),
    )
    op.create_table(
        "brand_assets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.PrimaryKeyConstraint("id", name="brand_assets_pkey"),
        sa.UniqueConstraint("name", name="brand_assets_name_key"),
    )
    op.create_table(
        "pitches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("prospect_text", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="pitches_created_by_fkey"),
        sa.PrimaryKeyConstraint("id", name="pitches_pkey"),
    )
    op.create_table(
        "pitch_inputs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pitch_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("file_name", sa.String(length=500), nullable=True),
        sa.Column("storage_key", sa.String(length=500), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="pitch_inputs_created_by_fkey"),
        sa.ForeignKeyConstraint(["pitch_id"], ["pitches.id"], name="pitch_inputs_pitch_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="pitch_inputs_pkey"),
        sa.UniqueConstraint("pitch_id", name="pitch_inputs_pitch_id_key"),
    )
    op.create_table(
        "pitch_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pitch_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["pitch_id"], ["pitches.id"], name="pitch_messages_pitch_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="pitch_messages_pkey"),
    )
    op.create_table(
        "pitch_matches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pitch_id", sa.Uuid(), nullable=False),
        sa.Column("study_id", sa.Uuid(), nullable=False),
        sa.Column("study_version_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source_quote", sa.Text(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["pitch_id"], ["pitches.id"], name="pitch_matches_pitch_id_fkey"),
        sa.ForeignKeyConstraint(["study_id"], ["studies.id"], name="pitch_matches_study_id_fkey"),
        sa.ForeignKeyConstraint(
            ["study_version_id"],
            ["study_versions.id"],
            name="pitch_matches_study_version_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="pitch_matches_pkey"),
    )
    op.create_table(
        "pitch_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pitch_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name="pitch_revisions_created_by_fkey"),
        sa.ForeignKeyConstraint(["pitch_id"], ["pitches.id"], name="pitch_revisions_pitch_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="pitch_revisions_pkey"),
    )
    op.create_table(
        "pitch_pages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("background", sa.Text(), nullable=False),
        sa.Column("challenges", sa.Text(), nullable=False),
        sa.Column("solution", sa.Text(), nullable=False),
        sa.Column("key_highlights", sa.Text(), nullable=False),
        sa.Column("impact", sa.Text(), nullable=False),
        sa.Column("technology", sa.Text(), nullable=False),
        sa.Column("prospect_logo_storage_key", sa.String(length=500), nullable=True),
        sa.ForeignKeyConstraint(["revision_id"], ["pitch_revisions.id"], name="pitch_pages_revision_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="pitch_pages_pkey"),
    )
    op.create_table(
        "pitch_pdfs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(length=500), nullable=False),
        sa.ForeignKeyConstraint(["revision_id"], ["pitch_revisions.id"], name="pitch_pdfs_revision_id_fkey"),
        sa.PrimaryKeyConstraint("id", name="pitch_pdfs_pkey"),
    )
    op.execute(
        """
        CREATE INDEX study_versions_summary_embedding_idx
        ON study_versions USING hnsw (summary_embedding vector_cosine_ops)
        WHERE is_current
        """
    )
    op.execute(
        """
        CREATE INDEX study_chunks_embedding_idx
        ON study_chunks USING hnsw (embedding vector_cosine_ops)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS study_chunks_embedding_idx")
    op.execute("DROP INDEX IF EXISTS study_versions_summary_embedding_idx")
    op.drop_table("pitch_pdfs")
    op.drop_table("pitch_pages")
    op.drop_table("pitch_revisions")
    op.drop_table("pitch_matches")
    op.drop_table("pitch_messages")
    op.drop_table("pitch_inputs")
    op.drop_table("pitches")
    op.drop_table("brand_assets")
    op.drop_table("study_images")
    op.drop_table("study_chunks")
    op.drop_table("study_versions")
    op.drop_table("studies")
    op.drop_table("deck_imports")
    op.drop_table("users")
