CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email TEXT NOT NULL UNIQUE,
  full_name TEXT,
  role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin', 'super_admin')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_login TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS documents (
  id BIGSERIAL PRIMARY KEY,
  source_type TEXT NOT NULL CHECK (source_type IN ('pdf', 'web')),
  source_name TEXT NOT NULL,
  source_url TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by UUID REFERENCES users(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'ready',
  text_chunk_count INTEGER NOT NULL DEFAULT 0,
  image_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS text_chunks (
  id BIGSERIAL PRIMARY KEY,
  document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_start INTEGER,
  page_end INTEGER,
  text TEXT NOT NULL,
  embedding VECTOR(3072),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS document_images (
  id BIGSERIAL PRIMARY KEY,
  document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  page_number INTEGER,
  storage_key TEXT NOT NULL,
  width INTEGER,
  height INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS image_captions (
  id BIGSERIAL PRIMARY KEY,
  image_id BIGINT NOT NULL REFERENCES document_images(id) ON DELETE CASCADE,
  caption_text TEXT NOT NULL,
  embedding VECTOR(3072),
  provider TEXT,
  model TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS ask_history (
  id BIGSERIAL PRIMARY KEY,
  user_id UUID REFERENCES users(id) ON DELETE SET NULL,
  user_email TEXT NOT NULL,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  conversation_id TEXT,
  documents_used JSONB NOT NULL DEFAULT '[]'::jsonb,
  chunks_used JSONB NOT NULL DEFAULT '[]'::jsonb,
  images_used JSONB NOT NULL DEFAULT '[]'::jsonb,
  webpage_links JSONB NOT NULL DEFAULT '[]'::jsonb,
  confidence_percent INTEGER NOT NULL,
  grounded BOOLEAN NOT NULL DEFAULT FALSE,
  retrieval_outcome TEXT NOT NULL,
  fallback_mode TEXT NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_documents_source_type ON documents (source_type);
CREATE INDEX IF NOT EXISTS idx_documents_source_url ON documents (source_url);
CREATE INDEX IF NOT EXISTS idx_text_chunks_document_id ON text_chunks (document_id);
CREATE INDEX IF NOT EXISTS idx_ask_history_user_email ON ask_history (user_email);
CREATE INDEX IF NOT EXISTS idx_ask_history_created_at ON ask_history (created_at DESC);
