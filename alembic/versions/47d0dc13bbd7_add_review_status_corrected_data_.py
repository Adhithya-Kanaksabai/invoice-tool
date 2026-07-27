"""add review_status corrected_data reviewed_at to documents

Revision ID: 47d0dc13bbd7
Revises: 444439395ac0
Create Date: 2026-07-27 23:58:45.117177

Adds the human-in-the-loop review columns to `documents`. See models.py's
Document docstring for the design intent (data stays immutable, corrected_data
holds human edits alongside it).

review_status is added with a server_default of 'pending' so any documents
already persisted before this migration backfill to "not yet reviewed" rather
than NULL — the app's own inserts use the model's Python-side default, this
server_default only exists to backfill existing rows on upgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "47d0dc13bbd7"
down_revision: str | Sequence[str] | None = "444439395ac0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "documents",
        sa.Column("review_status", sa.String(), nullable=False, server_default="pending"),
    )
    op.add_column("documents", sa.Column("corrected_data", sa.JSON(), nullable=True))
    op.add_column("documents", sa.Column("reviewed_at", sa.DateTime(), nullable=True))
    op.create_index(
        op.f("ix_documents_review_status"), "documents", ["review_status"], unique=False
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_documents_review_status"), table_name="documents")
    op.drop_column("documents", "reviewed_at")
    op.drop_column("documents", "corrected_data")
    op.drop_column("documents", "review_status")
