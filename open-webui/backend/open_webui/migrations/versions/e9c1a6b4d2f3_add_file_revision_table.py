"""Add file_revision table

Revision ID: e9c1a6b4d2f3
Revises: c8d3f1a2b4c5
Create Date: 2026-04-05 20:15:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e9c1a6b4d2f3"
down_revision: Union[str, None] = "c8d3f1a2b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "file_revision",
        sa.Column("id", sa.Text(), nullable=False, primary_key=True, unique=True),
        sa.Column(
            "file_id",
            sa.Text(),
            sa.ForeignKey("file.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("created_by", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint(
            "file_id", "revision", name="uq_file_revision_file_revision"
        ),
    )
    op.create_index("ix_file_revision_file_id", "file_revision", ["file_id"])
    op.create_index("ix_file_revision_revision", "file_revision", ["revision"])


def downgrade() -> None:
    op.drop_index("ix_file_revision_revision", table_name="file_revision")
    op.drop_index("ix_file_revision_file_id", table_name="file_revision")
    op.drop_table("file_revision")
