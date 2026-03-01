const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, init);
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return res.json() as Promise<T>;
}

// Health & readiness
export interface HealthResponse {
  ok: boolean;
  service: string;
}

export interface ReadyResponse {
  ok: boolean;
  postgres: { ok: boolean; [k: string]: unknown };
  redis: { ok: boolean; [k: string]: unknown };
}

export const fetchHealth = () => request<HealthResponse>("/health");
export const fetchReady = () => request<ReadyResponse>("/ready");

// Jobs
export interface JobListItem {
  job_id: string;
  state: string;
  node_id: string | null;
  gpu_ids: number[];
  timestamps: Record<string, number | null>;
  exit_code: number | null;
  reason: string | null;
}

export interface JobSummary {
  queued: number;
  placed: number;
  running: number;
  done: number;
  failed: number;
  cancelled: number;
}

export interface EnqueueResponse {
  job_id: string;
  created: boolean;
  status: {
    state: string;
    node_id: string | null;
    gpu_ids: number[];
    timestamps: Record<string, number | null>;
    exit_code: number | null;
    reason: string | null;
  };
}

export interface JobSpec {
  job_id: string;
  image: string;
  cmd: string[];
  gpus?: number;
  env?: Record<string, string>;
  metadata?: Record<string, unknown>;
}

export const fetchJobs = () => request<JobListItem[]>("/api/jobs");
export const fetchJobSummary = () => request<JobSummary>("/api/jobs/summary");
export const submitJob = (spec: JobSpec) =>
  request<EnqueueResponse>("/api/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(spec),
  });

// Nodes
export interface GpuInfo {
  index: number;
  name: string;
  mem_total_mb: number;
  utilization: number;
  mem_used_mb: number;
  temperature: number | null;
}

export interface NodeInfo {
  node_id: string;
  gpus: GpuInfo[];
  labels: Record<string, string>;
  agent_health: Record<string, number>;
  last_seen: number | null;
}

export const fetchNodes = () => request<NodeInfo[]>("/api/nodes");
