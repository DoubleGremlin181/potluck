"""Update face embeddings to 512 dimensions for facenet-pytorch.

Revision ID: 003_face_embeddings_512
Revises: 002_face_clustering
Create Date: 2026-01-02

This migration updates vector columns from 128 to 512 dimensions to support
facenet-pytorch which produces 512-dimensional embeddings.

Note: Existing face data is cleared as 128-dim embeddings are incompatible
with the new 512-dim format. Re-processing of media will be required.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003_face_embeddings_512"
down_revision: str | None = "002_face_clustering"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Step 1: Drop HNSW indexes (required before altering vector columns)
    op.execute("DROP INDEX IF EXISTS ix_face_clusters_encoding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_media_person_links_embedding_hnsw")

    # Step 2: Clear existing face data (incompatible with new dimensions)
    op.execute("DELETE FROM media_person_links WHERE embedding IS NOT NULL")
    op.execute("DELETE FROM face_clusters")
    op.execute("DELETE FROM face_encodings")

    # Step 3: Alter columns to 512 dimensions
    op.execute("ALTER TABLE face_encodings ALTER COLUMN embedding TYPE vector(512)")
    op.execute("ALTER TABLE face_clusters ALTER COLUMN representative_encoding TYPE vector(512)")
    op.execute("ALTER TABLE media_person_links ALTER COLUMN embedding TYPE vector(512)")

    # Step 4: Recreate HNSW indexes with new dimension
    op.execute(
        """
        CREATE INDEX ix_face_clusters_encoding_hnsw
        ON face_clusters
        USING hnsw (representative_encoding vector_cosine_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_media_person_links_embedding_hnsw
        ON media_person_links
        USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
        """
    )


def downgrade() -> None:
    # Drop HNSW indexes
    op.execute("DROP INDEX IF EXISTS ix_face_clusters_encoding_hnsw")
    op.execute("DROP INDEX IF EXISTS ix_media_person_links_embedding_hnsw")

    # Clear data (cannot convert 512-dim back to 128-dim)
    op.execute("DELETE FROM media_person_links WHERE embedding IS NOT NULL")
    op.execute("DELETE FROM face_clusters")
    op.execute("DELETE FROM face_encodings")

    # Revert to 128 dimensions
    op.execute("ALTER TABLE face_encodings ALTER COLUMN embedding TYPE vector(128)")
    op.execute("ALTER TABLE face_clusters ALTER COLUMN representative_encoding TYPE vector(128)")
    op.execute("ALTER TABLE media_person_links ALTER COLUMN embedding TYPE vector(128)")

    # Recreate indexes with original dimension
    op.execute(
        """
        CREATE INDEX ix_face_clusters_encoding_hnsw
        ON face_clusters
        USING hnsw (representative_encoding vector_cosine_ops)
        """
    )
    op.execute(
        """
        CREATE INDEX ix_media_person_links_embedding_hnsw
        ON media_person_links
        USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
        """
    )
