"""Add face clustering tables.

Revision ID: 002_face_clustering
Revises: 001_initial_schema
Create Date: 2025-12-30

This migration adds tables for Phase 4 face detection and clustering:
- face_clusters: Groups similar detected faces before Person assignment
- detected_faces: Individual faces detected in media items
- Updates media_person_links with detected_face_id reference
- Updates face_encodings to match new schema (reference embeddings for known people)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002_face_clustering"
down_revision: str | None = "001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # === Create face_clusters table ===
    op.create_table(
        "face_clusters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("representative_encoding", Vector(128), nullable=False),
        sa.Column(
            "status", sa.String(), nullable=False, server_default="pending"
        ),  # pending, confirmed, rejected
        sa.Column("person_id", sa.Uuid(), nullable=True),
        sa.Column("needs_review", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("face_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_face_clusters_person_id", "face_clusters", ["person_id"])
    op.create_index("ix_face_clusters_status", "face_clusters", ["status"])
    # HNSW index for face cluster similarity search
    op.execute(
        """
        CREATE INDEX ix_face_clusters_encoding_hnsw
        ON face_clusters
        USING hnsw (representative_encoding vector_cosine_ops)
        """
    )

    # === Create detected_faces table ===
    op.create_table(
        "detected_faces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("media_id", sa.Uuid(), nullable=False),
        sa.Column("cluster_id", sa.Uuid(), nullable=True),
        sa.Column("embedding", Vector(128), nullable=False),
        sa.Column("bbox_x", sa.Integer(), nullable=False),
        sa.Column("bbox_y", sa.Integer(), nullable=False),
        sa.Column("bbox_width", sa.Integer(), nullable=False),
        sa.Column("bbox_height", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.ForeignKeyConstraint(["cluster_id"], ["face_clusters.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_detected_faces_media_id", "detected_faces", ["media_id"])
    op.create_index("ix_detected_faces_cluster_id", "detected_faces", ["cluster_id"])
    # HNSW index for detected face similarity search
    op.execute(
        """
        CREATE INDEX ix_detected_faces_embedding_hnsw
        ON detected_faces
        USING hnsw (embedding vector_cosine_ops)
        """
    )

    # === Update media_person_links with detected_face_id ===
    op.add_column(
        "media_person_links",
        sa.Column("detected_face_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_media_person_links_detected_face_id",
        "media_person_links",
        "detected_faces",
        ["detected_face_id"],
        ["id"],
    )

    # === Update face_encodings table schema ===
    # The old schema had media_id (required) and bounding_box
    # The new schema has source_media_id (optional) and is_primary

    # Drop old foreign key constraint on media_id
    op.drop_constraint("face_encodings_media_id_fkey", "face_encodings", type_="foreignkey")
    op.drop_index("ix_face_encodings_media_id", table_name="face_encodings")

    # Drop old columns
    op.drop_column("face_encodings", "media_id")
    op.drop_column("face_encodings", "bounding_box")
    op.drop_column("face_encodings", "is_confirmed")

    # Add new columns
    op.add_column(
        "face_encodings",
        sa.Column("source_media_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "face_encodings",
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index("ix_face_encodings_source_media_id", "face_encodings", ["source_media_id"])


def downgrade() -> None:
    # === Revert face_encodings changes ===
    op.drop_index("ix_face_encodings_source_media_id", table_name="face_encodings")
    op.drop_column("face_encodings", "is_primary")
    op.drop_column("face_encodings", "source_media_id")

    # Re-add old columns
    op.add_column(
        "face_encodings",
        sa.Column("is_confirmed", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "face_encodings",
        sa.Column("bounding_box", sa.String(), nullable=True),
    )
    op.add_column(
        "face_encodings",
        sa.Column("media_id", sa.Uuid(), nullable=False),
    )
    op.create_index("ix_face_encodings_media_id", "face_encodings", ["media_id"])
    op.create_foreign_key(
        "face_encodings_media_id_fkey",
        "face_encodings",
        "media",
        ["media_id"],
        ["id"],
    )

    # === Remove detected_face_id from media_person_links ===
    op.drop_constraint(
        "fk_media_person_links_detected_face_id", "media_person_links", type_="foreignkey"
    )
    op.drop_column("media_person_links", "detected_face_id")

    # === Drop detected_faces table ===
    op.drop_index("ix_detected_faces_cluster_id", table_name="detected_faces")
    op.drop_index("ix_detected_faces_media_id", table_name="detected_faces")
    op.execute("DROP INDEX IF EXISTS ix_detected_faces_embedding_hnsw")
    op.drop_table("detected_faces")

    # === Drop face_clusters table ===
    op.drop_index("ix_face_clusters_status", table_name="face_clusters")
    op.drop_index("ix_face_clusters_person_id", table_name="face_clusters")
    op.execute("DROP INDEX IF EXISTS ix_face_clusters_encoding_hnsw")
    op.drop_table("face_clusters")
