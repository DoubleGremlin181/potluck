"""Initial schema with all Phase 2 models.

Revision ID: 001_initial_schema
Revises:
Create Date: 2025-12-20

"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Enable required PostgreSQL extensions
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')

    # === Import tracking tables ===

    op.create_table(
        "import_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("config", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "import_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("entities_found", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entities_created", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entities_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entities_skipped", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("entities_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_current", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress_total", sa.Integer(), nullable=True),
        sa.Column("current_file", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["import_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_runs_source_id", "import_runs", ["source_id"])

    # === People tables ===

    op.create_table(
        "people",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("is_self", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("merged_into_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["merged_into_id"], ["people.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "person_aliases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("alias_type", sa.String(), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.Column("normalized_value", sa.String(), nullable=True),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_person_aliases_person_id", "person_aliases", ["person_id"])
    op.create_index("ix_person_aliases_value", "person_aliases", ["value"])
    op.create_index("ix_person_aliases_normalized_value", "person_aliases", ["normalized_value"])

    # === Media tables ===

    op.create_table(
        "media",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("occurred_at_precision", sa.String(), nullable=False),
        sa.Column("source_timezone", sa.String(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("location_name", sa.String(), nullable=True),
        sa.Column("file_path", sa.String(), nullable=False),
        sa.Column("original_filename", sa.String(), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("media_type", sa.String(), nullable=False),
        sa.Column("file_hash", sa.String(), nullable=True),
        sa.Column("perceptual_hash", sa.String(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("exif_data", sa.String(), nullable=True),
        sa.Column("camera_make", sa.String(), nullable=True),
        sa.Column("camera_model", sa.String(), nullable=True),
        sa.Column("ocr_text", sa.String(), nullable=True),
        sa.Column("caption", sa.String(), nullable=True),
        sa.Column("transcript", sa.String(), nullable=True),
        sa.Column("source_url", sa.String(), nullable=True),
        sa.Column("album_name", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_content_hash", "media", ["content_hash"])
    op.create_index("ix_media_occurred_at", "media", ["occurred_at"])
    op.create_index("ix_media_file_path", "media", ["file_path"])
    op.create_index("ix_media_file_hash", "media", ["file_hash"])
    op.create_index("ix_media_perceptual_hash", "media", ["perceptual_hash"])

    op.create_table(
        "media_embeddings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("media_id", sa.Uuid(), nullable=False),
        sa.Column("embedding_type", sa.String(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("embedding", Vector(768), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_media_embeddings_media_id", "media_embeddings", ["media_id"])

    op.create_table(
        "face_encodings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("media_id", sa.Uuid(), nullable=False),
        sa.Column("embedding", Vector(128), nullable=False),
        sa.Column("bounding_box", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("is_confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_face_encodings_person_id", "face_encodings", ["person_id"])
    op.create_index("ix_face_encodings_media_id", "face_encodings", ["media_id"])

    op.create_table(
        "media_person_links",
        sa.Column("media_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("is_confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.PrimaryKeyConstraint("media_id", "person_id"),
    )

    # === Messages tables ===

    op.create_table(
        "chat_threads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("thread_type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("participant_count", sa.Integer(), nullable=True),
        sa.Column("participant_ids", sa.String(), nullable=True),
        sa.Column("first_message_at", sa.DateTime(), nullable=True),
        sa.Column("last_message_at", sa.DateTime(), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_muted", sa.Boolean(), nullable=False, server_default="false"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_threads_source_id", "chat_threads", ["source_id"])
    op.create_index("ix_chat_threads_last_message_at", "chat_threads", ["last_message_at"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("occurred_at_precision", sa.String(), nullable=False),
        sa.Column("source_timezone", sa.String(), nullable=True),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("sender_id", sa.Uuid(), nullable=True),
        sa.Column("sender_name", sa.String(), nullable=True),
        sa.Column("sender_phone", sa.String(), nullable=True),
        sa.Column("message_type", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=True),
        sa.Column("content_json", sa.String(), nullable=True),
        sa.Column("media_id", sa.Uuid(), nullable=True),
        sa.Column("media_caption", sa.String(), nullable=True),
        sa.Column("reply_to_id", sa.Uuid(), nullable=True),
        sa.Column("forwarded_from", sa.String(), nullable=True),
        sa.Column("is_from_me", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_starred", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reactions", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["thread_id"], ["chat_threads.id"]),
        sa.ForeignKeyConstraint(["sender_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.ForeignKeyConstraint(["reply_to_id"], ["chat_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chat_messages_thread_id", "chat_messages", ["thread_id"])
    op.create_index("ix_chat_messages_sender_id", "chat_messages", ["sender_id"])
    op.create_index("ix_chat_messages_occurred_at", "chat_messages", ["occurred_at"])
    op.create_index("ix_chat_messages_content_hash", "chat_messages", ["content_hash"])

    op.create_table(
        "chat_thread_participants",
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("nickname", sa.String(), nullable=True),
        sa.Column("joined_at", sa.DateTime(), nullable=True),
        sa.Column("left_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.ForeignKeyConstraint(["thread_id"], ["chat_threads.id"]),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.PrimaryKeyConstraint("thread_id", "person_id"),
    )

    # === Email tables ===

    op.create_table(
        "email_threads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("participant_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("participant_emails", sa.String(), nullable=True),
        sa.Column("email_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_email_at", sa.DateTime(), nullable=True),
        sa.Column("last_email_at", sa.DateTime(), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_starred", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_important", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("labels", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_threads_source_id", "email_threads", ["source_id"])
    op.create_index("ix_email_threads_last_email_at", "email_threads", ["last_email_at"])

    op.create_table(
        "emails",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("occurred_at_precision", sa.String(), nullable=False),
        sa.Column("source_timezone", sa.String(), nullable=True),
        sa.Column("thread_id", sa.Uuid(), nullable=True),
        sa.Column("message_id", sa.String(), nullable=True),
        sa.Column("in_reply_to", sa.String(), nullable=True),
        sa.Column("references", sa.String(), nullable=True),
        sa.Column("sender_id", sa.Uuid(), nullable=True),
        sa.Column("from_address", sa.String(), nullable=False),
        sa.Column("from_name", sa.String(), nullable=True),
        sa.Column("to_addresses", sa.String(), nullable=True),
        sa.Column("cc_addresses", sa.String(), nullable=True),
        sa.Column("bcc_addresses", sa.String(), nullable=True),
        sa.Column("reply_to_address", sa.String(), nullable=True),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("body_text", sa.String(), nullable=True),
        sa.Column("body_html", sa.String(), nullable=True),
        sa.Column("snippet", sa.String(), nullable=True),
        sa.Column("folder", sa.String(), nullable=False),
        sa.Column("labels", sa.String(), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_starred", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_important", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_draft", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_sent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_spam", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_trash", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("attachment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("has_attachments", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["thread_id"], ["email_threads.id"]),
        sa.ForeignKeyConstraint(["sender_id"], ["people.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_emails_thread_id", "emails", ["thread_id"])
    op.create_index("ix_emails_message_id", "emails", ["message_id"])
    op.create_index("ix_emails_sender_id", "emails", ["sender_id"])
    op.create_index("ix_emails_occurred_at", "emails", ["occurred_at"])
    op.create_index("ix_emails_content_hash", "emails", ["content_hash"])

    op.create_table(
        "email_attachments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email_id", sa.Uuid(), nullable=False),
        sa.Column("media_id", sa.Uuid(), nullable=True),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("mime_type", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("content_id", sa.String(), nullable=True),
        sa.Column("is_inline", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["email_id"], ["emails.id"]),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_attachments_email_id", "email_attachments", ["email_id"])

    # === Social tables ===

    op.create_table(
        "social_posts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("occurred_at_precision", sa.String(), nullable=False),
        sa.Column("source_timezone", sa.String(), nullable=True),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("post_type", sa.String(), nullable=False),
        sa.Column("post_id", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("permalink", sa.String(), nullable=True),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("author_name", sa.String(), nullable=True),
        sa.Column("is_own_post", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("community_name", sa.String(), nullable=True),
        sa.Column("community_id", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("body", sa.String(), nullable=True),
        sa.Column("body_html", sa.String(), nullable=True),
        sa.Column("link_url", sa.String(), nullable=True),
        sa.Column("link_domain", sa.String(), nullable=True),
        sa.Column("media_id", sa.Uuid(), nullable=True),
        sa.Column("thumbnail_url", sa.String(), nullable=True),
        sa.Column("media_urls", sa.String(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("upvotes", sa.Integer(), nullable=True),
        sa.Column("downvotes", sa.Integer(), nullable=True),
        sa.Column("comment_count", sa.Integer(), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=True),
        sa.Column("share_count", sa.Integer(), nullable=True),
        sa.Column("is_nsfw", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_spoiler", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_locked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_saved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_liked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("saved_at", sa.DateTime(), nullable=True),
        sa.Column("flair", sa.String(), nullable=True),
        sa.Column("tags", sa.String(), nullable=True),
        sa.Column("crosspost_parent_id", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["author_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["media_id"], ["media.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_social_posts_post_id", "social_posts", ["post_id"])
    op.create_index("ix_social_posts_author_id", "social_posts", ["author_id"])
    op.create_index("ix_social_posts_community_name", "social_posts", ["community_name"])
    op.create_index("ix_social_posts_occurred_at", "social_posts", ["occurred_at"])
    op.create_index("ix_social_posts_content_hash", "social_posts", ["content_hash"])

    op.create_table(
        "social_comments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("occurred_at_precision", sa.String(), nullable=False),
        sa.Column("source_timezone", sa.String(), nullable=True),
        sa.Column("post_id", sa.Uuid(), nullable=True),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("comment_id", sa.String(), nullable=True),
        sa.Column("permalink", sa.String(), nullable=True),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("author_name", sa.String(), nullable=True),
        sa.Column("is_own_comment", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("parent_comment_id", sa.Uuid(), nullable=True),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("body", sa.String(), nullable=True),
        sa.Column("body_html", sa.String(), nullable=True),
        sa.Column("post_title", sa.String(), nullable=True),
        sa.Column("community_name", sa.String(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column("upvotes", sa.Integer(), nullable=True),
        sa.Column("downvotes", sa.Integer(), nullable=True),
        sa.Column("is_edited", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("edited_at", sa.DateTime(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_stickied", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_saved", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_liked", sa.Boolean(), nullable=False, server_default="false"),
        sa.ForeignKeyConstraint(["post_id"], ["social_posts.id"]),
        sa.ForeignKeyConstraint(["author_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["parent_comment_id"], ["social_comments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_social_comments_post_id", "social_comments", ["post_id"])
    op.create_index("ix_social_comments_comment_id", "social_comments", ["comment_id"])
    op.create_index("ix_social_comments_author_id", "social_comments", ["author_id"])
    op.create_index("ix_social_comments_occurred_at", "social_comments", ["occurred_at"])
    op.create_index("ix_social_comments_content_hash", "social_comments", ["content_hash"])

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("subscription_type", sa.String(), nullable=False),
        sa.Column("target_id", sa.String(), nullable=True),
        sa.Column("target_name", sa.String(), nullable=False),
        sa.Column("target_url", sa.String(), nullable=True),
        sa.Column("target_description", sa.String(), nullable=True),
        sa.Column("subscribed_at", sa.DateTime(), nullable=True),
        sa.Column("unsubscribed_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("subscriber_count", sa.Integer(), nullable=True),
        sa.Column("post_count", sa.Integer(), nullable=True),
        sa.Column("notifications_enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_subscriptions_target_id", "subscriptions", ["target_id"])
    op.create_index("ix_subscriptions_target_name", "subscriptions", ["target_name"])

    # === Browsing tables ===

    op.create_table(
        "browsing_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("occurred_at_precision", sa.String(), nullable=False),
        sa.Column("source_timezone", sa.String(), nullable=True),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("url_hash", sa.String(), nullable=True),
        sa.Column("domain", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("favicon_url", sa.String(), nullable=True),
        sa.Column("visit_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("visit_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("transition_type", sa.String(), nullable=True),
        sa.Column("referrer_url", sa.String(), nullable=True),
        sa.Column("browser", sa.String(), nullable=True),
        sa.Column("device", sa.String(), nullable=True),
        sa.Column("search_query", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_browsing_history_url", "browsing_history", ["url"])
    op.create_index("ix_browsing_history_url_hash", "browsing_history", ["url_hash"])
    op.create_index("ix_browsing_history_domain", "browsing_history", ["domain"])
    op.create_index("ix_browsing_history_occurred_at", "browsing_history", ["occurred_at"])
    op.create_index("ix_browsing_history_content_hash", "browsing_history", ["content_hash"])

    op.create_table(
        "bookmark_folders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("full_path", sa.String(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("folder_created_at", sa.DateTime(), nullable=True),
        sa.Column("folder_modified_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["parent_id"], ["bookmark_folders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bookmark_folders_full_path", "bookmark_folders", ["full_path"])

    op.create_table(
        "bookmarks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("url_hash", sa.String(), nullable=True),
        sa.Column("domain", sa.String(), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("favicon_url", sa.String(), nullable=True),
        sa.Column("icon_uri", sa.String(), nullable=True),
        sa.Column("folder_id", sa.Uuid(), nullable=True),
        sa.Column("folder_path", sa.String(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.Column("bookmarked_at", sa.DateTime(), nullable=True),
        sa.Column("last_visited_at", sa.DateTime(), nullable=True),
        sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("tags", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["folder_id"], ["bookmark_folders.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bookmarks_url", "bookmarks", ["url"])
    op.create_index("ix_bookmarks_url_hash", "bookmarks", ["url_hash"])
    op.create_index("ix_bookmarks_domain", "bookmarks", ["domain"])
    op.create_index("ix_bookmarks_folder_id", "bookmarks", ["folder_id"])

    # === Notes tables ===

    op.create_table(
        "knowledge_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("occurred_at_precision", sa.String(), nullable=False),
        sa.Column("source_timezone", sa.String(), nullable=True),
        sa.Column("note_type", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("content", sa.String(), nullable=True),
        sa.Column("content_html", sa.String(), nullable=True),
        sa.Column("content_markdown", sa.String(), nullable=True),
        sa.Column("is_task", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_completed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("due_at", sa.DateTime(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("folder", sa.String(), nullable=True),
        sa.Column("labels", sa.String(), nullable=True),
        sa.Column("keywords", sa.String(), nullable=True),
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_trashed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("url", sa.String(), nullable=True),
        sa.Column("linked_media_ids", sa.String(), nullable=True),
        sa.Column("reminder_at", sa.DateTime(), nullable=True),
        sa.Column("has_reminder", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("embedding", Vector(768), nullable=True),
        sa.Column("color", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_knowledge_notes_occurred_at", "knowledge_notes", ["occurred_at"])
    op.create_index("ix_knowledge_notes_content_hash", "knowledge_notes", ["content_hash"])

    op.create_table(
        "note_checklists",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("note_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.String(), nullable=False),
        sa.Column("is_checked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["note_id"], ["knowledge_notes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_note_checklists_note_id", "note_checklists", ["note_id"])

    # === Location tables ===

    op.create_table(
        "locations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("location_type", sa.String(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("address", sa.String(), nullable=True),
        sa.Column("street", sa.String(), nullable=True),
        sa.Column("city", sa.String(), nullable=True),
        sa.Column("state", sa.String(), nullable=True),
        sa.Column("country", sa.String(), nullable=True),
        sa.Column("postal_code", sa.String(), nullable=True),
        sa.Column("place_id", sa.String(), nullable=True),
        sa.Column("google_maps_url", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("website", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_locations_city", "locations", ["city"])
    op.create_index("ix_locations_country", "locations", ["country"])
    op.create_index("ix_locations_place_id", "locations", ["place_id"])

    op.create_table(
        "location_visits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=True),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("accuracy_meters", sa.Float(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("place_name", sa.String(), nullable=True),
        sa.Column("address", sa.String(), nullable=True),
        sa.Column("place_id", sa.String(), nullable=True),
        sa.Column("activity_type", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["location_id"], ["locations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_location_visits_location_id", "location_visits", ["location_id"])
    op.create_index("ix_location_visits_started_at", "location_visits", ["started_at"])

    op.create_table(
        "location_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("altitude", sa.Float(), nullable=True),
        sa.Column("accuracy_meters", sa.Float(), nullable=True),
        sa.Column("vertical_accuracy", sa.Float(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("speed_mps", sa.Float(), nullable=True),
        sa.Column("heading", sa.Float(), nullable=True),
        sa.Column("device_id", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_location_history_timestamp", "location_history", ["timestamp"])

    # === Calendar tables ===

    op.create_table(
        "calendar_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=True),
        sa.Column("occurred_at_precision", sa.String(), nullable=False),
        sa.Column("source_timezone", sa.String(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("location_name", sa.String(), nullable=True),
        sa.Column("event_id", sa.String(), nullable=True),
        sa.Column("ical_uid", sa.String(), nullable=True),
        sa.Column("calendar_name", sa.String(), nullable=True),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("end_time", sa.DateTime(), nullable=True),
        sa.Column("is_all_day", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("timezone", sa.String(), nullable=True),
        sa.Column("is_recurring", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("recurrence_rule", sa.String(), nullable=True),
        sa.Column("recurring_event_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("visibility", sa.String(), nullable=False),
        sa.Column("location_string", sa.String(), nullable=True),
        sa.Column("organizer_id", sa.Uuid(), nullable=True),
        sa.Column("organizer_email", sa.String(), nullable=True),
        sa.Column("organizer_name", sa.String(), nullable=True),
        sa.Column("my_response_status", sa.String(), nullable=True),
        sa.Column("is_organizer", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("has_reminders", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("reminder_minutes", sa.String(), nullable=True),
        sa.Column("conference_url", sa.String(), nullable=True),
        sa.Column("conference_type", sa.String(), nullable=True),
        sa.Column("attachment_urls", sa.String(), nullable=True),
        sa.Column("event_created_at", sa.DateTime(), nullable=True),
        sa.Column("event_updated_at", sa.DateTime(), nullable=True),
        sa.Column("color", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["organizer_id"], ["people.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_calendar_events_event_id", "calendar_events", ["event_id"])
    op.create_index("ix_calendar_events_ical_uid", "calendar_events", ["ical_uid"])
    op.create_index("ix_calendar_events_start_time", "calendar_events", ["start_time"])
    op.create_index("ix_calendar_events_occurred_at", "calendar_events", ["occurred_at"])
    op.create_index("ix_calendar_events_content_hash", "calendar_events", ["content_hash"])

    op.create_table(
        "event_participants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("is_organizer", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_optional", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("response_status", sa.String(), nullable=False),
        sa.Column("comment", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["calendar_events.id"]),
        sa.ForeignKeyConstraint(["person_id"], ["people.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_event_participants_event_id", "event_participants", ["event_id"])
    op.create_index("ix_event_participants_person_id", "event_participants", ["person_id"])

    # === Financial tables ===

    op.create_table(
        "accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("account_type", sa.String(), nullable=False),
        sa.Column("institution", sa.String(), nullable=True),
        sa.Column("current_balance", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("currency", sa.String(), nullable=False, server_default="USD"),
        sa.Column("account_number_last4", sa.String(), nullable=True),
        sa.Column("is_closed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("closed_at", sa.DateTime(), nullable=True),
        sa.Column("is_on_budget", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_tracking", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notes", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_accounts_source_id", "accounts", ["source_id"])

    op.create_table(
        "transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=True),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_type", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("currency", sa.String(), nullable=False, server_default="USD"),
        sa.Column("payee", sa.String(), nullable=True),
        sa.Column("payee_id", sa.Uuid(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("original_description", sa.String(), nullable=True),
        sa.Column("category", sa.String(), nullable=True),
        sa.Column("category_group", sa.String(), nullable=True),
        sa.Column("subcategory", sa.String(), nullable=True),
        sa.Column("is_cleared", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_reconciled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_pending", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_flagged", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("flag_color", sa.String(), nullable=True),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("transfer_account_id", sa.Uuid(), nullable=True),
        sa.Column("transfer_transaction_id", sa.Uuid(), nullable=True),
        sa.Column("merchant_location", sa.String(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["payee_id"], ["people.id"]),
        sa.ForeignKeyConstraint(["transfer_account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["transfer_transaction_id"], ["transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_transactions_source_id", "transactions", ["source_id"])
    op.create_index("ix_transactions_account_id", "transactions", ["account_id"])
    op.create_index("ix_transactions_occurred_at", "transactions", ["occurred_at"])
    op.create_index("ix_transactions_payee", "transactions", ["payee"])
    op.create_index("ix_transactions_category", "transactions", ["category"])

    op.create_table(
        "budgets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("category_group", sa.String(), nullable=True),
        sa.Column("budgeted", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("spent", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0"),
        sa.Column("available", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column(
            "carryover", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0"
        ),
        sa.Column("currency", sa.String(), nullable=False, server_default="USD"),
        sa.Column("notes", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_budgets_year", "budgets", ["year"])
    op.create_index("ix_budgets_category", "budgets", ["category"])

    # === Entity links and tags tables ===

    op.create_table(
        "entity_links",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("target_type", sa.String(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("link_type", sa.String(), nullable=False),
        sa.Column("custom_type", sa.String(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("is_automatic", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("is_confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("notes", sa.String(), nullable=True),
        sa.Column("link_metadata", sa.String(), nullable=True),
        sa.Column("linker_name", sa.String(), nullable=True),
        sa.Column("linker_version", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_entity_links_source_id", "entity_links", ["source_id"])
    op.create_index("ix_entity_links_target_id", "entity_links", ["target_id"])

    op.create_table(
        "tags",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("color", sa.String(), nullable=True),
        sa.Column("icon", sa.String(), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("full_path", sa.String(), nullable=True),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default="false"),
        sa.ForeignKeyConstraint(["parent_id"], ["tags.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index("ix_tags_name", "tags", ["name"])
    op.create_index("ix_tags_full_path", "tags", ["full_path"])

    op.create_table(
        "tag_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("is_automatic", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("assigned_by", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tag_assignments_tag_id", "tag_assignments", ["tag_id"])
    op.create_index("ix_tag_assignments_entity_id", "tag_assignments", ["entity_id"])

    op.create_table(
        "tag_synonyms",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column("synonym", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("synonym"),
    )
    op.create_index("ix_tag_synonyms_tag_id", "tag_synonyms", ["tag_id"])
    op.create_index("ix_tag_synonyms_synonym", "tag_synonyms", ["synonym"])


def downgrade() -> None:
    # Drop tables in reverse order of creation (respecting foreign key constraints)
    op.drop_table("tag_synonyms")
    op.drop_table("tag_assignments")
    op.drop_table("tags")
    op.drop_table("entity_links")
    op.drop_table("budgets")
    op.drop_table("transactions")
    op.drop_table("accounts")
    op.drop_table("event_participants")
    op.drop_table("calendar_events")
    op.drop_table("location_history")
    op.drop_table("location_visits")
    op.drop_table("locations")
    op.drop_table("note_checklists")
    op.drop_table("knowledge_notes")
    op.drop_table("bookmarks")
    op.drop_table("bookmark_folders")
    op.drop_table("browsing_history")
    op.drop_table("subscriptions")
    op.drop_table("social_comments")
    op.drop_table("social_posts")
    op.drop_table("email_attachments")
    op.drop_table("emails")
    op.drop_table("email_threads")
    op.drop_table("chat_thread_participants")
    op.drop_table("chat_messages")
    op.drop_table("chat_threads")
    op.drop_table("media_person_links")
    op.drop_table("face_encodings")
    op.drop_table("media_embeddings")
    op.drop_table("media")
    op.drop_table("person_aliases")
    op.drop_table("people")
    op.drop_table("import_runs")
    op.drop_table("import_sources")
