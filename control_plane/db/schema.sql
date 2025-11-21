-- PostgreSQL schema for overlay scheduler (idempotent)
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
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
