"""Remove source_type column from media_person_links table.

Revision ID: 004_remove_source_type
Revises: 003_face_embeddings_512
Create Date: 2026-01-07

This migration removes the source_type column from media_person_links.
The column was incorrectly used to track face detection origin, but
SourceType should only be used for data ingestion sources.

MediaPersonLink entries are always created from face detection - tracking
the source is not necessary since it's implicit in the model's purpose.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004_remove_source_type"
down_revision: str | None = "003_face_embeddings_512"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("media_person_links", "source_type")


def downgrade() -> None:
    op.add_column(
        "media_person_links",
        sa.Column(
            "source_type",
            sa.String(50),
            nullable=False,
            server_default="face_detection",
        ),
    )
