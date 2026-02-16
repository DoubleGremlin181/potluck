"""Phase 8: New source types, entity types, and KnowledgeNote migration.

Revision ID: 002_phase8_source_types_and_notes
Revises: 001_initial_schema
Create Date: 2026-02-16

Changes:
1. Add IMAGE_FOLDER, TEXT_FILES, MBOX to sourcetype enum
2. Add SUBSCRIPTION, BUDGET to entitytype enum
3. Add source_type and source_id columns to knowledge_notes (BaseEntity migration)
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
    # 1. Add new SourceType enum values
    # PostgreSQL enums need explicit ALTER TYPE to add values
    op.execute("ALTER TYPE sourcetype ADD VALUE IF NOT EXISTS 'image_folder'")
    op.execute("ALTER TYPE sourcetype ADD VALUE IF NOT EXISTS 'text_files'")
    op.execute("ALTER TYPE sourcetype ADD VALUE IF NOT EXISTS 'mbox'")

    # 2. Add new EntityType enum values
    op.execute("ALTER TYPE entitytype ADD VALUE IF NOT EXISTS 'subscription'")
    op.execute("ALTER TYPE entitytype ADD VALUE IF NOT EXISTS 'budget'")

    # 3. Add source_type and source_id to knowledge_notes (BaseEntity fields)
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
    # Remove added columns from knowledge_notes
    op.drop_column("knowledge_notes", "source_id")
    op.drop_column("knowledge_notes", "source_type")

    # Note: PostgreSQL doesn't support removing enum values easily.
    # In practice, downgrading enums requires recreating the type.
    # For simplicity, we leave the enum values in place on downgrade.
