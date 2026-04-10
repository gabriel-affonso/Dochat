"""add dochat resource state columns

Revision ID: c8d3f1a2b4c5
Revises: b2c3d4e5f6a7
Create Date: 2026-03-19 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c8d3f1a2b4c5"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("note", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_pinned",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(
            sa.Column(
                "is_archived",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("archived_at", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("last_opened_at", sa.BigInteger(), nullable=True))

    with op.batch_alter_table("chat", schema=None) as batch_op:
        batch_op.add_column(sa.Column("archived_at", sa.BigInteger(), nullable=True))

    with op.batch_alter_table("file", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_archived",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("archived_at", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("file", schema=None) as batch_op:
        batch_op.drop_column("archived_at")
        batch_op.drop_column("is_archived")

    with op.batch_alter_table("chat", schema=None) as batch_op:
        batch_op.drop_column("archived_at")

    with op.batch_alter_table("note", schema=None) as batch_op:
        batch_op.drop_column("last_opened_at")
        batch_op.drop_column("archived_at")
        batch_op.drop_column("is_archived")
        batch_op.drop_column("is_pinned")
