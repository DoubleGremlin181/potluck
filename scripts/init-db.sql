-- Initialize PostgreSQL with required extensions for Potluck
-- This script runs automatically when the database container starts

-- Enable pgvector for vector similarity search
CREATE EXTENSION IF NOT EXISTS vector;

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enable pg_tde for transparent data encryption (Percona-specific)
CREATE EXTENSION IF NOT EXISTS pg_tde;

-- =============================================================================
-- Encryption Key Provider Setup
-- =============================================================================
-- This configures a FILE-BASED key provider for development/testing.
--
-- WARNING: File-based keys are NOT recommended for production!
-- Keys are stored unencrypted on disk at /var/lib/postgresql/data/pg_tde/
--
-- For production, configure HashiCorp Vault AFTER initial setup:
--   1. Run initial setup with file-based keys
--   2. Export your data
--   3. Drop and recreate database with Vault:
--      SELECT pg_tde_add_database_key_provider_vault_v2(
--          'vault-provider',
--          'https://vault.example.com:8200',
--          'your-token',
--          'secret'
--      );
--      SELECT pg_tde_set_key_using_database_key_provider('potluck-key', 'vault-provider');
--   4. Re-import your data
--
-- See: https://docs.percona.com/pg-tde/global-key-provider-configuration/vault.html
-- =============================================================================

-- Set up file-based key provider (development/testing only)
-- Keyring stored in data directory so it persists across container restarts
-- Note: Percona PostgreSQL uses /data/db as the data directory
SELECT pg_tde_add_database_key_provider_file(
    'file-provider',
    '/data/db/pg_tde_keyring.per'
);
SELECT pg_tde_create_key_using_database_key_provider('potluck-key', 'file-provider');
SELECT pg_tde_set_key_using_database_key_provider('potluck-key', 'file-provider');

-- Note: default_table_access_method = 'tde_heap' is set in Dockerfile.db
-- This ensures all new tables are encrypted automatically
