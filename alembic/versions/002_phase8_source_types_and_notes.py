"""Phase 8: KnowledgeNote migration to BaseEntity.

Revision ID: 002_phase8_source_types_and_notes
Revises: 001_initial_schema
Create Date: 2026-02-16

Changes:
1. Add source_type and source_id columns to knowledge_notes (BaseEntity migration)

Note: SourceType and EntityType are stored as VARCHAR strings (not PostgreSQL enums),
so new enum values (IMAGE_FOLDER, TEXT_FILES, MBOX, SUBSCRIPTION, BUDGET) require
no database-level changes — only Python-side additions to the enum classes.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002_phase8_source_types_and_notes"
down_revision: str = "001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add source_type and source_id to knowledge_notes (BaseEntity fields)
    # content_hash column already exists from original schema
    op.add_column(
        "knowledge_notes",
        sa.Column(
            "source_type",
            sa.String(),
            nullable=False,
            server_default="manual",
        ),
    )
    op.add_column(
        "knowledge_notes",
        sa.Column("source_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_notes", "source_id")
    op.drop_column("knowledge_notes", "source_type")
