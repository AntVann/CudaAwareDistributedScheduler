const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

type RequestOptions = RequestInit & {
  token?: string;
};

async function request<T>(path: string, init?: RequestOptions): Promise<T> {
  const headers = new Headers(init?.headers);
  if (init?.token) {
    headers.set("Authorization", `Bearer ${init.token}`);
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });
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
export const submitJob = (spec: JobSpec, token: string) =>
  request<EnqueueResponse>("/api/jobs", {
    method: "POST",
    token,
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

// Metrics
export interface MetricsSummary {
  queue_depth: number;
  jobs: JobSummary;
  nodes: {
    total: number;
    fresh: number;
    stale: number;
  };
  latency_ms: {
    placement_p50: number;
    placement_p95: number;
    run_p50: number;
    run_p95: number;
  };
  windowed_terminal_counts: {
    done: number;
    failed: number;
  };
  window_minutes: number;
}

export const fetchMetricsSummary = (windowMinutes = 60) =>
  request<MetricsSummary>(`/api/metrics/summary?window_minutes=${windowMinutes}`);

// Policies
export interface PoliciesResponse {
  active: string;
  supported: string[];
}

export const fetchPolicies = () => request<PoliciesResponse>("/api/policies");
export const updateActivePolicy = (policy: string, token: string) =>
  request<PoliciesResponse>("/api/policies/active", {
    method: "PUT",
    token,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ policy }),
  });
