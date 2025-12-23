"""End-to-end tests for project setup and database initialization.

These tests use the main docker-compose.yml and scripts/setup.sh to verify that:
1. Docker containers start correctly (Percona PostgreSQL 17 with pgvector + pg_tde)
2. PostgreSQL extensions are installed (vector, pg_tde, uuid-ossp)
3. Alembic migrations run successfully
4. All expected tables are created with correct columns and constraints

Run with: pytest tests/integration/ -v --run-e2e
Skip with: pytest tests/ (excludes integration by default)
"""

import psycopg2
import pytest

# Mark all tests in this module as e2e (requires --run-e2e flag)
pytestmark = pytest.mark.e2e


class TestDatabaseExtensions:
    """Test that required PostgreSQL extensions are installed."""

    def test_vector_extension_installed(
        self,
        run_migrations: None,  # noqa: ARG002 - ensures migrations run first
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify pgvector extension is installed for vector similarity search."""
        with db_connection.cursor() as cursor:
            cursor.execute(
                "SELECT extname FROM pg_extension WHERE extname = 'vector'"
            )
            result = cursor.fetchone()
            assert result is not None, "pgvector extension not installed"
            assert result[0] == "vector"

    def test_uuid_extension_installed(
        self,
        run_migrations: None,  # noqa: ARG002 - ensures migrations run first
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify uuid-ossp extension is installed for UUID generation."""
        with db_connection.cursor() as cursor:
            cursor.execute(
                "SELECT extname FROM pg_extension WHERE extname = 'uuid-ossp'"
            )
            result = cursor.fetchone()
            assert result is not None, "uuid-ossp extension not installed"
            assert result[0] == "uuid-ossp"

    def test_pg_tde_extension_installed(
        self,
        run_migrations: None,  # noqa: ARG002 - ensures migrations run first
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify pg_tde extension is installed for transparent data encryption.

        Requires:
        - Percona PostgreSQL 17+ image
        - ENABLE_PG_TDE=1 environment variable
        - Key provider configuration in init-db.sql
        """
        with db_connection.cursor() as cursor:
            cursor.execute(
                "SELECT extname FROM pg_extension WHERE extname = 'pg_tde'"
            )
            result = cursor.fetchone()
            assert result is not None, (
                "pg_tde extension not installed - ensure using Percona PostgreSQL 17 "
                "with ENABLE_PG_TDE=1"
            )
            assert result[0] == "pg_tde"


class TestAlembicMigrations:
    """Test that Alembic migrations run successfully."""

    def test_migrations_complete(
        self,
        run_migrations: None,  # noqa: ARG002 - fixture runs migrations
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify migrations ran and alembic_version table exists."""
        with db_connection.cursor() as cursor:
            cursor.execute(
                "SELECT version_num FROM alembic_version"
            )
            result = cursor.fetchone()
            assert result is not None, "No migration version found"
            assert result[0] == "001_initial_schema", (
                f"Expected migration 001_initial_schema, got {result[0]}"
            )


class TestExpectedTables:
    """Test that all expected tables are created by migrations."""

    # All tables that should be created by the initial migration
    EXPECTED_TABLES = [
        # Import tracking
        "import_sources",
        "import_runs",
        # People
        "people",
        "person_aliases",
        # Media
        "media",
        "media_embeddings",
        "face_encodings",
        "media_person_links",
        # Messages
        "chat_threads",
        "chat_messages",
        "chat_thread_participants",
        # Email
        "email_threads",
        "emails",
        "email_attachments",
        # Social
        "social_posts",
        "social_comments",
        "subscriptions",
        # Browsing
        "browsing_history",
        "bookmark_folders",
        "bookmarks",
        # Notes
        "knowledge_notes",
        "note_checklists",
        # Locations
        "locations",
        "location_visits",
        "location_history",
        # Calendar
        "calendar_events",
        "event_participants",
        # Financial
        "accounts",
        "transactions",
        "budgets",
        # Links and tags
        "entity_links",
        "tags",
        "tag_assignments",
        "tag_synonyms",
    ]

    def test_all_tables_exist(
        self,
        run_migrations: None,  # noqa: ARG002 - fixture runs migrations
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify all expected tables are created."""
        with db_connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public'
                ORDER BY tablename
                """
            )
            actual_tables = {row[0] for row in cursor.fetchall()}

        # Check each expected table exists
        missing_tables = set(self.EXPECTED_TABLES) - actual_tables
        assert not missing_tables, f"Missing tables: {missing_tables}"

    @pytest.mark.parametrize("table_name", EXPECTED_TABLES)
    def test_table_exists(
        self,
        run_migrations: None,  # noqa: ARG002 - fixture runs migrations
        db_connection: psycopg2.extensions.connection,
        table_name: str,
    ) -> None:
        """Verify each individual table exists."""
        with db_connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT FROM pg_tables
                    WHERE schemaname = 'public' AND tablename = %s
                )
                """,
                (table_name,),
            )
            exists = cursor.fetchone()[0]
            assert exists, f"Table '{table_name}' does not exist"


class TestTableColumns:
    """Test that key tables have expected columns."""

    def test_people_table_columns(
        self,
        run_migrations: None,  # noqa: ARG002 - fixture runs migrations
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify people table has all expected columns."""
        expected_columns = {
            "id", "created_at", "updated_at", "display_name", "photo_url",
            "date_of_birth", "notes", "is_self", "merged_into_id",
        }
        self._check_table_columns(db_connection, "people", expected_columns)

    def test_media_table_columns(
        self,
        run_migrations: None,  # noqa: ARG002 - fixture runs migrations
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify media table has all expected columns including geolocation."""
        expected_columns = {
            "id", "created_at", "updated_at", "source_type", "source_id",
            "content_hash", "occurred_at", "occurred_at_precision", "source_timezone",
            "latitude", "longitude", "altitude", "location_name", "tags",
            "file_path", "original_filename", "file_size", "mime_type", "media_type",
            "file_hash", "perceptual_hash", "width", "height", "duration_seconds",
            "exif_data", "camera_make", "camera_model", "ocr_text", "caption",
            "transcript", "source_url", "album_name",
        }
        self._check_table_columns(db_connection, "media", expected_columns)

    def test_knowledge_notes_table_columns(
        self,
        run_migrations: None,  # noqa: ARG002 - fixture runs migrations
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify knowledge_notes table has nullable source_type (FlexibleEntity)."""
        with db_connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'knowledge_notes' AND column_name = 'source_type'
                """
            )
            result = cursor.fetchone()
            assert result is not None, "source_type column not found"
            assert result[1] == "YES", (
                "source_type should be nullable for FlexibleEntity"
            )

    def test_accounts_table_columns(
        self,
        run_migrations: None,  # noqa: ARG002 - fixture runs migrations
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify accounts table has all expected columns."""
        expected_columns = {
            "id", "created_at", "updated_at", "source_type", "source_id",
            "content_hash", "tags", "name", "account_type", "institution",
            "current_balance", "currency", "account_number_last4", "is_closed",
            "closed_at", "is_on_budget", "is_tracking", "notes",
        }
        self._check_table_columns(db_connection, "accounts", expected_columns)

    def test_transactions_table_columns(
        self,
        run_migrations: None,  # noqa: ARG002 - fixture runs migrations
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify transactions table has simplified structure (no subcategory)."""
        expected_columns = {
            "id", "created_at", "updated_at", "source_type", "source_id",
            "content_hash", "tags", "occurred_at", "occurred_at_precision",
            "source_timezone", "account_id", "transaction_type", "amount",
            "currency", "payee", "payee_id", "description", "original_description",
            "category", "category_group", "is_cleared", "is_reconciled",
            "is_pending", "is_transfer", "transfer_account_id",
            "merchant_location", "latitude", "longitude",
        }
        self._check_table_columns(db_connection, "transactions", expected_columns)

        # Verify subcategory was NOT added (per PR feedback)
        with db_connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'transactions' AND column_name = 'subcategory'
                """
            )
            result = cursor.fetchone()
            assert result is None, "subcategory column should not exist"

    def test_calendar_events_table_columns(
        self,
        run_migrations: None,  # noqa: ARG002 - fixture runs migrations
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify calendar_events has location_text (not location_string)."""
        with db_connection.cursor() as cursor:
            # Should have location_text
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'calendar_events' AND column_name = 'location_text'
                """
            )
            result = cursor.fetchone()
            assert result is not None, "location_text column should exist"

            # Should NOT have location_string
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'calendar_events' AND column_name = 'location_string'
                """
            )
            result = cursor.fetchone()
            assert result is None, "location_string should not exist (renamed to location_text)"

    def _check_table_columns(
        self,
        conn: psycopg2.extensions.connection,
        table_name: str,
        expected_columns: set[str],
    ) -> None:
        """Helper to verify a table has expected columns."""
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_name = %s
                """,
                (table_name,),
            )
            actual_columns = {row[0] for row in cursor.fetchall()}

        missing = expected_columns - actual_columns
        assert not missing, f"Table '{table_name}' missing columns: {missing}"


class TestVectorColumns:
    """Test that vector columns are properly created for pgvector."""

    def test_media_embeddings_has_vector_column(
        self,
        run_migrations: None,  # noqa: ARG002 - fixture runs migrations
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify media_embeddings table has vector column for embeddings."""
        with db_connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT data_type, udt_name
                FROM information_schema.columns
                WHERE table_name = 'media_embeddings' AND column_name = 'embedding'
                """
            )
            result = cursor.fetchone()
            assert result is not None, "embedding column not found"
            # pgvector stores as USER-DEFINED type
            assert result[0] == "USER-DEFINED" and result[1] == "vector", (
                f"Expected vector type, got {result}"
            )

    def test_face_encodings_has_vector_column(
        self,
        run_migrations: None,  # noqa: ARG002 - fixture runs migrations
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify face_encodings table has vector column."""
        with db_connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT data_type, udt_name
                FROM information_schema.columns
                WHERE table_name = 'face_encodings' AND column_name = 'embedding'
                """
            )
            result = cursor.fetchone()
            assert result is not None, "embedding column not found"
            assert result[0] == "USER-DEFINED" and result[1] == "vector", (
                f"Expected vector type, got {result}"
            )

    def test_knowledge_notes_has_vector_column(
        self,
        run_migrations: None,  # noqa: ARG002 - fixture runs migrations
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify knowledge_notes table has optional vector column."""
        with db_connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT data_type, udt_name, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'knowledge_notes' AND column_name = 'embedding'
                """
            )
            result = cursor.fetchone()
            assert result is not None, "embedding column not found"
            assert result[0] == "USER-DEFINED" and result[1] == "vector", (
                f"Expected vector type, got {result}"
            )
            assert result[2] == "YES", "embedding should be nullable"


class TestHNSWIndexes:
    """Test that HNSW indexes are created for vector similarity search."""

    def test_media_embeddings_hnsw_index(
        self,
        run_migrations: None,  # noqa: ARG002 - fixture runs migrations
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify HNSW index exists on media_embeddings."""
        self._check_hnsw_index(
            db_connection,
            "ix_media_embeddings_embedding_hnsw",
            "media_embeddings",
        )

    def test_face_encodings_hnsw_index(
        self,
        run_migrations: None,  # noqa: ARG002 - fixture runs migrations
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify HNSW index exists on face_encodings."""
        self._check_hnsw_index(
            db_connection,
            "ix_face_encodings_embedding_hnsw",
            "face_encodings",
        )

    def test_knowledge_notes_hnsw_index(
        self,
        run_migrations: None,  # noqa: ARG002 - fixture runs migrations
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify HNSW index exists on knowledge_notes (partial index)."""
        self._check_hnsw_index(
            db_connection,
            "ix_knowledge_notes_embedding_hnsw",
            "knowledge_notes",
        )

    def _check_hnsw_index(
        self,
        conn: psycopg2.extensions.connection,
        index_name: str,
        table_name: str,
    ) -> None:
        """Helper to verify an HNSW index exists."""
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE tablename = %s AND indexname = %s
                """,
                (table_name, index_name),
            )
            result = cursor.fetchone()
            assert result is not None, f"Index '{index_name}' not found"
            assert "hnsw" in result[1].lower(), (
                f"Expected HNSW index, got: {result[1]}"
            )


class TestTableEncryption:
    """Test that tables are encrypted with pg_tde.

    Per https://docs.percona.com/pg-tde/test.html, we can verify encryption
    using the pg_tde_is_encrypted() function.
    """

    # Key tables to verify encryption (a representative subset)
    ENCRYPTED_TABLES = [
        "people",
        "media",
        "chat_messages",
        "emails",
        "transactions",
        "knowledge_notes",
    ]

    @pytest.mark.parametrize("table_name", ENCRYPTED_TABLES)
    def test_table_is_encrypted(
        self,
        run_migrations: None,  # noqa: ARG002 - ensures migrations run first
        db_connection: psycopg2.extensions.connection,
        table_name: str,
    ) -> None:
        """Verify each table is encrypted with pg_tde."""
        with db_connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_tde_is_encrypted(%s)",
                (table_name,),
            )
            result = cursor.fetchone()
            assert result is not None, f"Could not check encryption for '{table_name}'"
            assert result[0] is True, (
                f"Table '{table_name}' is not encrypted. "
                "Ensure default_table_access_method is set to 'tde_heap'."
            )

    def test_all_user_tables_encrypted(
        self,
        run_migrations: None,  # noqa: ARG002 - ensures migrations run first
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify all user tables (except alembic_version) are encrypted."""
        with db_connection.cursor() as cursor:
            # Get all user tables
            cursor.execute(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public' AND tablename != 'alembic_version'
                ORDER BY tablename
                """
            )
            tables = [row[0] for row in cursor.fetchall()]

        unencrypted = []
        for table in tables:
            with db_connection.cursor() as cursor:
                cursor.execute("SELECT pg_tde_is_encrypted(%s)", (table,))
                result = cursor.fetchone()
                if result is None or result[0] is not True:
                    unencrypted.append(table)

        assert not unencrypted, (
            f"The following tables are not encrypted: {unencrypted}. "
            "All tables should use tde_heap access method."
        )


class TestForeignKeys:
    """Test that foreign key constraints are properly created."""

    def test_import_runs_references_import_sources(
        self,
        run_migrations: None,  # noqa: ARG002 - fixture runs migrations
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify import_runs.source_id references import_sources.id."""
        self._check_foreign_key(
            db_connection,
            "import_runs",
            "source_id",
            "import_sources",
            "id",
        )

    def test_transactions_references_accounts(
        self,
        run_migrations: None,  # noqa: ARG002 - fixture runs migrations
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify transactions.account_id references accounts.id."""
        self._check_foreign_key(
            db_connection,
            "transactions",
            "account_id",
            "accounts",
            "id",
        )

    def test_chat_messages_references_threads(
        self,
        run_migrations: None,  # noqa: ARG002 - fixture runs migrations
        db_connection: psycopg2.extensions.connection,
    ) -> None:
        """Verify chat_messages.thread_id references chat_threads.id."""
        self._check_foreign_key(
            db_connection,
            "chat_messages",
            "thread_id",
            "chat_threads",
            "id",
        )

    def _check_foreign_key(
        self,
        conn: psycopg2.extensions.connection,
        source_table: str,
        source_column: str,
        target_table: str,
        target_column: str,
    ) -> None:
        """Helper to verify a foreign key constraint exists."""
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    kcu.column_name,
                    ccu.table_name AS foreign_table,
                    ccu.column_name AS foreign_column
                FROM information_schema.key_column_usage kcu
                JOIN information_schema.constraint_column_usage ccu
                    ON kcu.constraint_name = ccu.constraint_name
                JOIN information_schema.table_constraints tc
                    ON kcu.constraint_name = tc.constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                    AND kcu.table_name = %s
                    AND kcu.column_name = %s
                """,
                (source_table, source_column),
            )
            result = cursor.fetchone()
            assert result is not None, (
                f"No foreign key from {source_table}.{source_column}"
            )
            assert result[1] == target_table, (
                f"Expected FK to {target_table}, got {result[1]}"
            )
            assert result[2] == target_column, (
                f"Expected FK column {target_column}, got {result[2]}"
            )
