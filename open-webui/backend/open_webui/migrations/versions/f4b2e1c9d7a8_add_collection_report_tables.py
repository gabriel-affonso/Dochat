"""Add collection synthesis report tables

Revision ID: f4b2e1c9d7a8
Revises: e9c1a6b4d2f3
Create Date: 2026-04-08 11:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f4b2e1c9d7a8"
down_revision: Union[str, None] = "e9c1a6b4d2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "collection_report",
        sa.Column("id", sa.Text(), nullable=False, primary_key=True, unique=True),
        sa.Column(
            "collection_id",
            sa.Text(),
            sa.ForeignKey("knowledge.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column(
            "note_id",
            sa.Text(),
            sa.ForeignKey("note.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("model_name", sa.Text(), nullable=True),
        sa.Column("chunk_config", sa.JSON(), nullable=True),
        sa.Column("documents_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("documents_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_step", sa.Text(), nullable=True),
        sa.Column("current_document_id", sa.Text(), nullable=True),
        sa.Column("current_document_title", sa.Text(), nullable=True),
        sa.Column("included_document_ids", sa.JSON(), nullable=True),
        sa.Column("failed_documents", sa.JSON(), nullable=True),
        sa.Column("final_report", sa.JSON(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.BigInteger(), nullable=True),
        sa.Column("completed_at", sa.BigInteger(), nullable=True),
    )
    op.create_index(
        "ix_collection_report_collection_id", "collection_report", ["collection_id"]
    )

    op.create_table(
        "document_summary",
        sa.Column("id", sa.Text(), nullable=False, primary_key=True, unique=True),
        sa.Column(
            "collection_report_id",
            sa.Text(),
            sa.ForeignKey("collection_report.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.Text(),
            sa.ForeignKey("file.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("document_updated_at", sa.BigInteger(), nullable=True),
        sa.Column("text_available", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("chunks", sa.JSON(), nullable=True),
        sa.Column("chunk_summaries", sa.JSON(), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("warnings", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            "collection_report_id",
            "document_id",
            name="uq_document_summary_report_document",
        ),
    )
    op.create_index(
        "ix_document_summary_report_id", "document_summary", ["collection_report_id"]
    )
    op.create_index("ix_document_summary_document_id", "document_summary", ["document_id"])


def downgrade() -> None:
    op.drop_index("ix_document_summary_document_id", table_name="document_summary")
    op.drop_index("ix_document_summary_report_id", table_name="document_summary")
    op.drop_table("document_summary")

    op.drop_index("ix_collection_report_collection_id", table_name="collection_report")
    op.drop_table("collection_report")
