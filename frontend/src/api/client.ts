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
  postgres: { ok: boolean; mode?: string; path?: string; [k: string]: unknown };
  redis: { ok: boolean; mode?: string; [k: string]: unknown };
}

export const fetchHealth = () => request<HealthResponse>("/health");
export const fetchReady = () => request<ReadyResponse>("/ready");

export interface MeResponse {
  subject: string;
  role: "admin" | "user";
  projects: string[];
  expires_at: string | null;
}

export const fetchMe = (token: string) =>
  request<MeResponse>("/api/me", {
    token,
  });

// Jobs
export interface PlacementCandidate {
  node_id: string;
  gpu_count: number;
  available_gpu: number;
  avg_utilization: number;
  partitions: string[];
  state: string;
  eligible: boolean;
  selected: boolean;
  rejected_reason?: string;
  score?: number;
}

export interface PlacementDecision {
  policy: string;
  partition: string | null;
  requested_gpus: number;
  /**
   * The node selected for this job. `null` means the scheduler tried but
   * found no eligible candidate — the job stayed QUEUED. Inspect
   * `candidates[*].rejected_reason` to see why.
   */
  chosen_node_id: string | null;
  chosen_reason: string;
  candidates: PlacementCandidate[];
  decided_at: number;
  round_robin_pointer?: number;
}

export interface JobListItem {
  job_id: string;
  project: string;
  state: string;
  backend_ref: string | null;
  node_id: string | null;
  gpu_ids: number[];
  timestamps: Record<string, number | null>;
  exit_code: number | null;
  reason: string | null;
  placement_decision: PlacementDecision | null;
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
    project: string | null;
    node_id: string | null;
    gpu_ids: number[];
    timestamps: Record<string, number | null>;
    exit_code: number | null;
    reason: string | null;
  };
}

export interface JobSpec {
  job_id: string;
  project: string;
  image: string;
  cmd: string[];
  gpus?: number;
  cpu?: number;
  mem_gb?: number;
  priority?: number;
  env?: Record<string, string>;
  metadata?: Record<string, unknown>;
}

export interface JobLogsResponse {
  stream: "stdout" | "stderr";
  path: string;
  exists: boolean;
  content: string;
  lines: number;
  bytes_total: number;
  truncated: boolean;
  error?: string;
}

export const fetchJobs = (token: string) => request<JobListItem[]>("/api/jobs", { token });
export const fetchJobSummary = (token: string) => request<JobSummary>("/api/jobs/summary", { token });
export const submitJob = (spec: JobSpec, token: string) =>
  request<EnqueueResponse>("/api/jobs", {
    method: "POST",
    token,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(spec),
  });
export const fetchJobLogs = (
  jobId: string,
  stream: "stdout" | "stderr",
  token: string,
  tail = 200,
) =>
  request<JobLogsResponse>(
    `/api/jobs/${encodeURIComponent(jobId)}/logs?stream=${stream}&tail=${tail}`,
    { token },
  );

export interface CancelJobResponse {
  state: string;
  node_id: string | null;
  gpu_ids: number[];
  timestamps: Record<string, number | null>;
  exit_code: number | null;
  reason: string | null;
}

export const cancelJob = (jobId: string, token: string) =>
  request<CancelJobResponse>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: "POST",
    token,
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

export const fetchNodes = (token: string) => request<NodeInfo[]>("/api/nodes", { token });

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

export const fetchMetricsSummary = (windowMinutes = 60, token?: string) =>
  request<MetricsSummary>(`/api/metrics/summary?window_minutes=${windowMinutes}`, { token });

// Policies
export interface PoliciesResponse {
  active: string;
  supported: string[];
}

export const fetchPolicies = (token: string) => request<PoliciesResponse>("/api/policies", { token });
export const updateActivePolicy = (policy: string, token: string) =>
  request<PoliciesResponse>("/api/policies/active", {
    method: "PUT",
    token,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ policy }),
  });

// Token requests
export interface TokenRequestPayload {
  subject_name: string;
  email: string;
  requested_projects: string[];
  purpose: string;
}

export interface TokenRequestItem {
  request_id: string;
  subject_name: string;
  email: string;
  requested_projects: string[];
  purpose: string;
  status: "PENDING" | "APPROVED" | "REJECTED";
  review_notes: string | null;
  reviewed_by: string | null;
  created_at: string | null;
  reviewed_at: string | null;
}

export interface TokenInfo {
  token_id: string;
  subject: string;
  role: string;
  projects: string[];
  active: boolean;
  expires_at: string | null;
  created_at: string | null;
  created_by: string | null;
}

export interface ApproveTokenRequestResponse {
  request_id: string;
  status: string;
  token_id: string;
  expires_at: string;
  plaintext_token?: string;
}

export const submitTokenRequest = (payload: TokenRequestPayload) =>
  request<{ request_id: string; status: string }>("/api/token-requests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

export const fetchTokenRequests = (token: string, status = "PENDING") =>
  request<TokenRequestItem[]>(`/api/admin/token-requests?status=${encodeURIComponent(status)}`, {
    token,
  });

export const approveTokenRequest = (requestId: string, token: string, reviewNotes = "") =>
  request<ApproveTokenRequestResponse>(
    `/api/admin/token-requests/${requestId}/approve`,
    {
      method: "POST",
      token,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ review_notes: reviewNotes }),
    }
  );

export const rejectTokenRequest = (requestId: string, token: string, reviewNotes = "") =>
  request<{ request_id: string; status: string }>(`/api/admin/token-requests/${requestId}/reject`, {
    method: "POST",
    token,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ review_notes: reviewNotes }),
  });

export const fetchTokens = (token: string) => request<TokenInfo[]>("/api/admin/tokens", { token });

export const revokeToken = (tokenId: string, token: string, reason = "") =>
  request<{ token_id: string; revoked: boolean }>(`/api/admin/tokens/${tokenId}/revoke`, {
    method: "POST",
    token,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason }),
  });
