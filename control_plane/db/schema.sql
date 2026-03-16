-- PostgreSQL schema for overlay scheduler (idempotent)
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  project TEXT NOT NULL DEFAULT 'default',
  submitted_by TEXT,
  spec JSONB NOT NULL,
  status TEXT NOT NULL,
  node_id TEXT,
  gpu_ids INT[],
  timestamps JSONB,
  exit_code INT,
  reason TEXT
);

CREATE TABLE IF NOT EXISTS nodes (
  node_id TEXT PRIMARY KEY,
  labels JSONB,
  gpus JSONB,
  agent_health JSONB,
  last_seen TIMESTAMP
);

CREATE TABLE IF NOT EXISTS events (
  id BIGSERIAL PRIMARY KEY,
  ts TIMESTAMP NOT NULL DEFAULT NOW(),
  job_id TEXT,
  kind TEXT NOT NULL,
  payload JSONB
);

CREATE TABLE IF NOT EXISTS scheduler_settings (
  singleton_key TEXT PRIMARY KEY,
  active_policy TEXT NOT NULL,
  updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
  updated_by TEXT
);

CREATE TABLE IF NOT EXISTS api_tokens (
  id UUID PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  subject TEXT NOT NULL,
  role TEXT NOT NULL,
  projects JSONB NOT NULL DEFAULT '[]'::jsonb,
  active BOOLEAN NOT NULL DEFAULT TRUE,
  expires_at TIMESTAMP,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  created_by TEXT
);

CREATE TABLE IF NOT EXISTS token_requests (
  id UUID PRIMARY KEY,
  subject_name TEXT NOT NULL,
  email TEXT NOT NULL,
  requested_projects JSONB NOT NULL DEFAULT '[]'::jsonb,
  purpose TEXT NOT NULL,
  status TEXT NOT NULL,
  review_notes TEXT,
  reviewed_by TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT NOW(),
  reviewed_at TIMESTAMP
);

ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS project TEXT;
ALTER TABLE jobs
  ADD COLUMN IF NOT EXISTS submitted_by TEXT;
UPDATE jobs
SET project = 'default'
WHERE project IS NULL;
ALTER TABLE jobs
  ALTER COLUMN project SET DEFAULT 'default';
ALTER TABLE jobs
  ALTER COLUMN project SET NOT NULL;
