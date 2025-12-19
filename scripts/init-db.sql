-- Initialize PostgreSQL with required extensions for Potluck

-- Enable pgvector for vector similarity search
CREATE EXTENSION IF NOT EXISTS vector;

-- Enable pg_tde for transparent data encryption
-- Note: pg_tde requires additional configuration in postgresql.conf
-- This extension enables encryption-at-rest for the database
CREATE EXTENSION IF NOT EXISTS pg_tde;

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
