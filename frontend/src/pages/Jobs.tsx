import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import Card from "../components/Card";
import Icon from "../components/Icon";
import StatusBadge from "../components/StatusBadge";
import { useOperatorAuth } from "../auth-context";
import {
  cancelJob,
  fetchJobLogs,
  fetchJobs,
  submitJob,
  type EnqueueResponse,
  type JobListItem,
  type JobLogsResponse,
} from "../api/client";

const CANCELLABLE_STATES = new Set(["QUEUED", "PLACED", "RUNNING"]);

function formatTs(ts: number | null | undefined): string {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleString();
}

function truncate(s: string | null | undefined, max = 60): string {
  if (!s) return "-";
  return s.length > max ? s.slice(0, max) + "..." : s;
}

function JobLogsPanel({
  jobId,
  hasBackendRef,
  token,
}: {
  jobId: string;
  hasBackendRef: boolean;
  token: string;
}) {
  const [stream, setStream] = useState<"stdout" | "stderr">("stderr");
  const [logs, setLogs] = useState<JobLogsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadLogs = useCallback(
    async (which: "stdout" | "stderr") => {
      setLoading(true);
      setError(null);
      try {
        const data = await fetchJobLogs(jobId, which, token, 200);
        setLogs(data);
        setStream(which);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load logs");
      } finally {
        setLoading(false);
      }
    },
    [jobId, token],
  );

  if (!hasBackendRef) {
    return (
      <div className="mt-4 rounded-md border border-border bg-surface-1 p-3 text-xs text-text-muted">
        No backend job reference yet — logs are available once the job is dispatched.
      </div>
    );
  }

  if (!token) {
    return (
      <div className="mt-4 rounded-md border border-border bg-surface-1 p-3 text-xs text-text-muted">
        Bearer token required to fetch logs.
      </div>
    );
  }

  return (
    <div className="mt-4 rounded-md border border-border bg-surface-1 p-3">
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-medium text-text-secondary">Logs</span>
        <div className="flex gap-2">
          <button
            onClick={() => loadLogs("stderr")}
            className={`rounded px-2 py-1 text-xs ${
              stream === "stderr" && logs
                ? "bg-accent text-white"
                : "border border-border text-text-secondary hover:bg-surface-2"
            }`}
          >
            stderr
          </button>
          <button
            onClick={() => loadLogs("stdout")}
            className={`rounded px-2 py-1 text-xs ${
              stream === "stdout" && logs
                ? "bg-accent text-white"
                : "border border-border text-text-secondary hover:bg-surface-2"
            }`}
          >
            stdout
          </button>
          {logs && (
            <button
              onClick={() => loadLogs(stream)}
              className="rounded border border-border px-2 py-1 text-xs text-text-secondary hover:bg-surface-2"
            >
              ↻ Refresh
            </button>
          )}
        </div>
      </div>
      {error && <p className="text-xs text-state-failed">{error}</p>}
      {loading && <p className="text-xs text-text-muted">Loading {stream}...</p>}
      {!loading && !logs && !error && (
        <p className="text-xs text-text-muted">Click stderr or stdout to view the tail.</p>
      )}
      {logs && (
        <div className="space-y-2">
          <p className="font-mono text-[11px] text-text-muted break-all">
            {logs.path}
            {logs.exists
              ? ` · ${logs.lines} line${logs.lines === 1 ? "" : "s"}${
                  logs.truncated ? " (tail)" : ""
                } · ${logs.bytes_total} bytes`
              : " · file not yet written"}
          </p>
          {logs.exists ? (
            <pre className="max-h-80 overflow-auto rounded bg-surface-0 p-2 font-mono text-[11px] text-text-secondary whitespace-pre-wrap">
              {logs.content || "(empty)"}
            </pre>
          ) : (
            <p className="text-xs text-text-muted">
              SLURM hasn't written this stream yet. Try refresh once the job is RUNNING or terminal.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

const PARTITION_OPTIONS = [
  { value: "", label: "Auto-select" },
  { value: "gpuqs", label: "gpuqs - Short GPU (2d)" },
  { value: "gpuqm", label: "gpuqm - Medium GPU (7d)" },
  { value: "gpuql", label: "gpuql - Long GPU (14d)" },
  { value: "nsfqs", label: "nsfqs - NSF Short GPU (2d)" },
  { value: "nsfqm", label: "nsfqm - NSF Medium GPU (14d)" },
  { value: "nsfql", label: "nsfql - NSF Long GPU (21d)" },
];

export default function Jobs() {
  const { token, me, loadingMe } = useOperatorAuth();
  const [jobs, setJobs] = useState<JobListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Default ON: this is a live ops dashboard, stale rows are misleading.
  // Polling tick is 3s in the effect below; users can toggle off with the checkbox.
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Filter / search / sort state. State multi-select defaults to all-on, search
  // is a substring match on job_id, sort toggles enqueued newest/oldest.
  const ALL_STATES = ["QUEUED", "PLACED", "RUNNING", "DONE", "FAILED", "CANCELLED"];
  const [stateFilter, setStateFilter] = useState<Set<string>>(new Set(ALL_STATES));
  const [searchQuery, setSearchQuery] = useState("");
  const [sortDir, setSortDir] = useState<"newest" | "oldest">("newest");
  // Time window in hours; null = all time. Default 24h matches typical "what
  // happened today" workflow without losing access to older rows.
  const TIME_WINDOWS: { value: number | null; label: string }[] = [
    { value: 1, label: "1h" },
    { value: 24, label: "24h" },
    { value: 24 * 7, label: "7d" },
    { value: null, label: "all" },
  ];
  const [windowHours, setWindowHours] = useState<number | null>(24);

  const toggleStateFilter = (state: string) => {
    setStateFilter((prev) => {
      const next = new Set(prev);
      if (next.has(state)) {
        next.delete(state);
      } else {
        next.add(state);
      }
      return next;
    });
  };

  // Submit form state
  const [showSubmit, setShowSubmit] = useState(false);
  const [project, setProject] = useState("");
  const [cmd, setCmd] = useState('["echo", "hello"]');
  const [gpus, setGpus] = useState(1);
  const [partition, setPartition] = useState("");
  // Optional advanced fields (#13). Empty string = leave the JobSpec field
  // unset so the backend keeps its defaults.
  const [cpuStr, setCpuStr] = useState("");
  const [memGbStr, setMemGbStr] = useState("");
  const [priorityStr, setPriorityStr] = useState("");
  const [envText, setEnvText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitResult, setSubmitResult] = useState<{ msg: string; ok: boolean } | null>(null);

  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadJobs = useCallback(async () => {
    if (!token) {
      setJobs([]);
      setError("Enter a valid token to load jobs.");
      setLoading(false);
      return;
    }
    try {
      const data = await fetchJobs(token);
      setJobs(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load jobs");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void loadJobs();
  }, [loadJobs]);

  useEffect(() => {
    if (!me) return;
    if (me.role === "user") {
      if (me.projects.length === 0) {
        setProject("");
        return;
      }
      setProject((prev) => (me.projects.includes(prev) ? prev : me.projects[0]));
      return;
    }
    setProject((prev) => prev || "default");
  }, [me]);

  useEffect(() => {
    if (autoRefresh) {
      timerRef.current = setInterval(() => {
        void loadJobs();
      }, 3000);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [autoRefresh, loadJobs]);

  const [cancellingId, setCancellingId] = useState<string | null>(null);
  const [cancelError, setCancelError] = useState<string | null>(null);

  const handleCancel = async (jobId: string) => {
    if (!token) return;
    if (!window.confirm(`Cancel job ${jobId}? This sends scancel to SLURM.`)) {
      return;
    }
    setCancellingId(jobId);
    setCancelError(null);
    try {
      await cancelJob(jobId, token);
      void loadJobs();
    } catch (err) {
      setCancelError(err instanceof Error ? err.message : "Cancel failed");
    } finally {
      setCancellingId(null);
    }
  };

  const handleSubmit = async () => {
    setSubmitting(true);
    setSubmitResult(null);
    try {
      const parsedCmd: string[] = JSON.parse(cmd);
      const jobId = `job-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      const metadata: Record<string, unknown> = {};
      if (partition) {
        metadata.partition = partition;
      }

      // Parse optional advanced fields. Empty / whitespace = leave undefined.
      const cpu = cpuStr.trim() ? parseInt(cpuStr, 10) : undefined;
      const memGb = memGbStr.trim() ? parseFloat(memGbStr) : undefined;
      const priority = priorityStr.trim() ? parseInt(priorityStr, 10) : undefined;
      const env: Record<string, string> = {};
      for (const rawLine of envText.split("\n")) {
        const line = rawLine.trim();
        if (!line || line.startsWith("#")) continue;
        const eq = line.indexOf("=");
        if (eq <= 0) {
          throw new Error(
            `Env line not in KEY=VALUE form: ${line}. Use one VAR=value per line, # for comments.`,
          );
        }
        const key = line.slice(0, eq).trim();
        const value = line.slice(eq + 1);
        env[key] = value;
      }

      const spec: import("../api/client").JobSpec = {
        job_id: jobId,
        project: project.trim(),
        image: "",
        cmd: parsedCmd,
        gpus,
        metadata,
      };
      if (cpu !== undefined && !Number.isNaN(cpu)) spec.cpu = cpu;
      if (memGb !== undefined && !Number.isNaN(memGb)) spec.mem_gb = memGb;
      if (priority !== undefined && !Number.isNaN(priority)) spec.priority = priority;
      if (Object.keys(env).length > 0) spec.env = env;

      const res: EnqueueResponse = await submitJob(spec, token);
      setSubmitResult({
        msg: res.created
          ? `Created job ${res.job_id} (201)`
          : `Job ${res.job_id} already exists (200)`,
        ok: true,
      });
      void loadJobs();
    } catch (err) {
      setSubmitResult({
        msg: err instanceof Error ? err.message : "Submit failed",
        ok: false,
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="page fade-in">
      <div className="page-header">
        <div>
          <h1 className="page-title">Jobs</h1>
          <div className="page-sub">
            {autoRefresh ? "Auto-refreshing every 3s." : "Auto-refresh paused."}
          </div>
        </div>
        <div className="page-actions">
          <label className="toggle">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            <span className="track">
              <span className="thumb" />
            </span>
            Auto-refresh
          </label>
          <button
            type="button"
            onClick={() => setShowSubmit(!showSubmit)}
            className="btn btn-primary"
          >
            + Submit Job
          </button>
        </div>
      </div>

      {/* Submit panel */}
      {showSubmit && (
        <Card>
          <h3 className="text-sm font-medium text-text-secondary mb-3">Submit a Job</h3>
          <p className="mb-3 text-xs text-text-muted">
            Requires a valid user/admin token in the sidebar. Advanced fields (CPU,
            memory, priority, env) are optional — leave blank to use backend defaults.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-3">
            <div>
              <label className="block text-xs text-text-muted mb-1">Project</label>
              {me?.role === "user" ? (
                <select
                  value={project}
                  onChange={(e) => setProject(e.target.value)}
                  disabled={loadingMe || me.projects.length === 0}
                  className="mb-3 w-full rounded-md border border-border bg-surface-0 px-3 py-2 text-sm font-mono text-text-primary focus:outline-none focus:border-accent disabled:opacity-60"
                >
                  {me.projects.length === 0 ? (
                    <option value="">
                      {loadingMe ? "Loading allowed projects..." : "No allowed projects assigned"}
                    </option>
                  ) : (
                    me.projects.map((p) => (
                      <option key={p} value={p}>
                        {p}
                      </option>
                    ))
                  )}
                </select>
              ) : (
                <input
                  type="text"
                  value={project}
                  onChange={(e) => setProject(e.target.value)}
                  className="mb-3 w-full rounded-md border border-border bg-surface-0 px-3 py-2 text-sm font-mono text-text-primary focus:outline-none focus:border-accent"
                />
              )}
              <label className="block text-xs text-text-muted mb-1">Command (JSON array)</label>
              <input
                type="text"
                value={cmd}
                onChange={(e) => setCmd(e.target.value)}
                className="w-full rounded-md border border-border bg-surface-0 px-3 py-2 text-sm font-mono text-text-primary focus:outline-none focus:border-accent"
              />
            </div>
            <div className="flex gap-3">
              <div className="flex-1">
                <label className="block text-xs text-text-muted mb-1">GPUs</label>
                <input
                  type="number"
                  min={1}
                  max={16}
                  value={gpus}
                  onChange={(e) => setGpus(Math.max(1, parseInt(e.target.value) || 1))}
                  className="w-full rounded-md border border-border bg-surface-0 px-3 py-2 text-sm font-mono text-text-primary focus:outline-none focus:border-accent"
                />
              </div>
              <div className="flex-1">
                <label className="block text-xs text-text-muted mb-1">Partition</label>
                <select
                  value={partition}
                  onChange={(e) => setPartition(e.target.value)}
                  className="w-full rounded-md border border-border bg-surface-0 px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-accent"
                >
                  {PARTITION_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          {/* Advanced fields — match the JobSpec model 1:1 */}
          <details className="mb-3 rounded-md border border-border bg-surface-0 px-3 py-2">
            <summary className="cursor-pointer text-xs font-medium text-text-secondary">
              Advanced (CPU / Memory / Priority / Env)
            </summary>
            <div className="mt-3 grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div>
                <label className="block text-xs text-text-muted mb-1">CPUs</label>
                <input
                  type="number"
                  min={1}
                  placeholder="default"
                  value={cpuStr}
                  onChange={(e) => setCpuStr(e.target.value)}
                  className="w-full rounded-md border border-border bg-surface-1 px-3 py-2 text-sm font-mono text-text-primary focus:outline-none focus:border-accent"
                />
              </div>
              <div>
                <label className="block text-xs text-text-muted mb-1">Memory (GB)</label>
                <input
                  type="number"
                  min={0}
                  step="0.5"
                  placeholder="default"
                  value={memGbStr}
                  onChange={(e) => setMemGbStr(e.target.value)}
                  className="w-full rounded-md border border-border bg-surface-1 px-3 py-2 text-sm font-mono text-text-primary focus:outline-none focus:border-accent"
                />
              </div>
              <div>
                <label className="block text-xs text-text-muted mb-1">Priority</label>
                <input
                  type="number"
                  placeholder="0"
                  value={priorityStr}
                  onChange={(e) => setPriorityStr(e.target.value)}
                  className="w-full rounded-md border border-border bg-surface-1 px-3 py-2 text-sm font-mono text-text-primary focus:outline-none focus:border-accent"
                />
              </div>
            </div>
            <div className="mt-3">
              <label className="block text-xs text-text-muted mb-1">
                Env (one VAR=value per line, # for comments)
              </label>
              <textarea
                value={envText}
                onChange={(e) => setEnvText(e.target.value)}
                rows={4}
                placeholder={"# example\nCUDA_VISIBLE_DEVICES=0,1\nMY_FLAG=1"}
                className="w-full rounded-md border border-border bg-surface-1 px-3 py-2 text-xs font-mono text-text-primary focus:outline-none focus:border-accent"
              />
            </div>
          </details>
          <div className="flex items-center gap-3">
            <button
              onClick={handleSubmit}
              disabled={submitting || !token || !project.trim()}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50 transition-colors"
            >
              {submitting ? "Submitting..." : token ? "Submit" : "Token Required"}
            </button>
            {submitResult && (
              <p className={`text-xs font-mono ${submitResult.ok ? "text-state-done" : "text-state-failed"}`}>
                {submitResult.msg}
              </p>
            )}
          </div>
        </Card>
      )}

      {error && (
        <div className="rounded-md border border-state-failed/30 bg-state-failed/10 px-4 py-3 text-sm text-state-failed">
          {error}
        </div>
      )}

      {/* Filter / search / sort bar — only shown when there are jobs to filter.
          Uses the design-system .chip and .input primitives so it picks up the
          orange accent on selection and the active-filter tags pattern. */}
      {!loading && jobs.length > 0 && (
        <div className="card mb-4">
          <div className="card-body" style={{ padding: "14px 18px" }}>
            <div
              className="flex items-center gap-3"
              style={{ flexWrap: "wrap" }}
            >
              <div style={{ position: "relative", minWidth: 240, flex: "1 1 240px" }}>
                <span
                  style={{
                    position: "absolute",
                    left: 10,
                    top: 8,
                    color: "var(--color-text-3)",
                    pointerEvents: "none",
                  }}
                >
                  <Icon name="search" size={14} />
                </span>
                <input
                  className="input mono"
                  placeholder="Search by job_id…"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  style={{ paddingLeft: 32 }}
                />
              </div>
              <div
                style={{ width: 1, background: "var(--color-border)", height: 24 }}
              />
              <span className="text-xs muted font-medium">State</span>
              {ALL_STATES.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => toggleStateFilter(s)}
                  className={"chip" + (stateFilter.has(s) ? " active" : "")}
                >
                  {s}
                </button>
              ))}
              <div
                style={{ width: 1, background: "var(--color-border)", height: 24 }}
              />
              <span className="text-xs muted font-medium">Window</span>
              {TIME_WINDOWS.map((w) => (
                <button
                  key={w.label}
                  type="button"
                  onClick={() => setWindowHours(w.value)}
                  className={"chip" + (windowHours === w.value ? " active" : "")}
                  title={
                    w.value === null
                      ? "Show all jobs regardless of age"
                      : `Show jobs enqueued in the last ${w.label}`
                  }
                >
                  {w.label}
                </button>
              ))}
              <div className="grow" />
              <button
                type="button"
                onClick={() => setSortDir((d) => (d === "newest" ? "oldest" : "newest"))}
                className="btn btn-ghost btn-sm"
                title="Toggle sort direction"
              >
                Enqueued {sortDir === "newest" ? "↓ newest" : "↑ oldest"}
              </button>
            </div>

            {/* Active-filter row — only renders when a filter is actually
                applied. Each tag is removable and there's a clear-all link. */}
            {(stateFilter.size < ALL_STATES.length || searchQuery.trim()) && (
              <div
                className="flex items-center gap-2 mt-3"
                style={{ flexWrap: "wrap" }}
              >
                <span className="text-xs muted">Active filters:</span>
                {ALL_STATES.filter((s) => !stateFilter.has(s)).length > 0 &&
                  ALL_STATES.filter((s) => stateFilter.has(s)).map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => toggleStateFilter(s)}
                      className="chip active"
                    >
                      {s}
                      <span className="chip-x">
                        <Icon name="x" size={11} />
                      </span>
                    </button>
                  ))}
                {searchQuery.trim() && (
                  <button
                    type="button"
                    onClick={() => setSearchQuery("")}
                    className="chip active"
                  >
                    “{searchQuery.trim()}”
                    <span className="chip-x">
                      <Icon name="x" size={11} />
                    </span>
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => {
                    setStateFilter(new Set(ALL_STATES));
                    setSearchQuery("");
                  }}
                >
                  Clear all
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Jobs table */}
      {(() => {
        const q = searchQuery.trim().toLowerCase();
        const cutoffSecs =
          windowHours === null ? null : Date.now() / 1000 - windowHours * 3600;
        const filtered = jobs
          .filter((j) => stateFilter.has(j.state))
          .filter((j) => !q || j.job_id.toLowerCase().includes(q))
          .filter((j) => {
            if (cutoffSecs === null) return true;
            const enq = j.timestamps?.enqueued ?? 0;
            return enq >= cutoffSecs;
          });
        const sorted = [...filtered].sort((a, b) => {
          const ta = a.timestamps?.enqueued ?? 0;
          const tb = b.timestamps?.enqueued ?? 0;
          return sortDir === "newest" ? (tb || 0) - (ta || 0) : (ta || 0) - (tb || 0);
        });

        if (loading) {
          return <p className="text-sm text-text-muted">Loading...</p>;
        }
        if (jobs.length === 0) {
          return (
            <Card>
              <p className="text-sm text-text-muted text-center py-4">
                No jobs found. Submit a test job to get started.
              </p>
            </Card>
          );
        }
        if (sorted.length === 0) {
          return (
            <Card>
              <p className="text-sm text-text-muted text-center py-4">
                No jobs match the current filter ({jobs.length} total). Try adjusting state or search.
              </p>
            </Card>
          );
        }
        return (
          <div className="space-y-2">
            <p className="text-xs muted">
              Showing {sorted.length} of {jobs.length}
              {sorted.length !== jobs.length ? " (filtered)" : ""}.
            </p>
            <div className="card" style={{ overflow: "hidden" }}>
              <table className="t-table">
                <thead>
                  <tr>
                    <th>Job ID</th>
                    <th>SLURM ID</th>
                    <th>State</th>
                    <th>Project</th>
                    <th>Node</th>
                    <th>Exit Code</th>
                    <th>Reason</th>
                    <th className="px-4 py-2.5 font-medium">Enqueued</th>
                  </tr>
                </thead>
                <tbody>
              {sorted.map((job) => (
                <Fragment key={job.job_id}>
                  <tr
                    onClick={() => setExpandedId(expandedId === job.job_id ? null : job.job_id)}
                    className="border-b border-border hover:bg-surface-2/50 cursor-pointer transition-colors"
                  >
                    <td className="px-4 py-2.5 font-mono text-xs text-accent">{job.job_id}</td>
                    <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                      {job.backend_ref ? `#${job.backend_ref}` : "-"}
                    </td>
                    <td className="px-4 py-2.5">
                      <StatusBadge state={job.state} />
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                      {job.project}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                      {job.node_id ?? "-"}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-text-secondary">
                      {job.exit_code ?? "-"}
                    </td>
                    <td className="px-4 py-2.5 text-xs text-text-muted">
                      {truncate(job.reason)}
                    </td>
                    <td className="px-4 py-2.5 font-mono text-xs text-text-muted">
                      {formatTs(job.timestamps?.enqueued)}
                    </td>
                  </tr>
                  {expandedId === job.job_id && (
                    <tr className="border-b border-border bg-surface-2/30">
                      <td colSpan={8} className="px-6 py-4">
                        {CANCELLABLE_STATES.has(job.state) && (
                          <div className="mb-3 flex items-center gap-3">
                            <button
                              onClick={() => handleCancel(job.job_id)}
                              disabled={!token || cancellingId === job.job_id}
                              title={
                                !token ? "Operator token required" : undefined
                              }
                              className="rounded-md border border-state-failed/40 bg-state-failed/10 px-3 py-1.5 text-xs font-medium text-state-failed hover:bg-state-failed/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                            >
                              {cancellingId === job.job_id
                                ? "Cancelling..."
                                : token
                                  ? "Cancel job"
                                  : "Cancel (token required)"}
                            </button>
                            {cancelError && cancellingId === null && (
                              <span className="text-xs text-state-failed">
                                {cancelError}
                              </span>
                            )}
                          </div>
                        )}
                        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-xs">
                          <div>
                            <span className="text-text-muted">GPU IDs:</span>{" "}
                            <span className="font-mono text-text-secondary">
                              {job.gpu_ids.length ? job.gpu_ids.join(", ") : "-"}
                            </span>
                          </div>
                          {job.backend_ref && (
                            <div>
                              <span className="text-text-muted">SLURM Job ID:</span>{" "}
                              <span className="font-mono text-text-secondary">{job.backend_ref}</span>
                            </div>
                          )}
                          {Object.entries(job.timestamps).map(([key, val]) => (
                            <div key={key}>
                              <span className="text-text-muted">{key}:</span>{" "}
                              <span className="font-mono text-text-secondary">{formatTs(val)}</span>
                            </div>
                          ))}
                          {job.reason && (
                            <div className="col-span-full">
                              <span className="text-text-muted">Reason:</span>{" "}
                              <span className="font-mono text-text-secondary">{job.reason}</span>
                            </div>
                          )}
                        </div>

                        <JobLogsPanel jobId={job.job_id} hasBackendRef={!!job.backend_ref} token={token} />

                        {job.placement_decision && (
                          <div className="mt-4 rounded-md border border-border bg-surface-1 p-3">
                            <div className="mb-2 flex items-center gap-2 text-xs font-medium">
                              <span className="text-text-secondary">
                                Placement decision
                              </span>
                              {job.placement_decision.chosen_node_id === null && (
                                <span className="rounded border border-state-queued/40 bg-state-queued/10 px-2 py-0.5 text-[10px] text-state-queued">
                                  STUCK · no eligible nodes
                                </span>
                              )}
                            </div>
                            <div className="mb-3 grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                              <div>
                                <span className="text-text-muted">Policy:</span>{" "}
                                <span className="font-mono text-accent">
                                  {job.placement_decision.policy}
                                </span>
                              </div>
                              <div>
                                <span className="text-text-muted">Partition:</span>{" "}
                                <span className="font-mono text-text-secondary">
                                  {job.placement_decision.partition ?? "any"}
                                </span>
                              </div>
                              <div>
                                <span className="text-text-muted">Requested GPUs:</span>{" "}
                                <span className="font-mono text-text-secondary">
                                  {job.placement_decision.requested_gpus}
                                </span>
                              </div>
                              <div>
                                <span className="text-text-muted">Decided at:</span>{" "}
                                <span className="font-mono text-text-secondary">
                                  {formatTs(job.placement_decision.decided_at)}
                                </span>
                              </div>
                              <div className="col-span-full">
                                <span className="text-text-muted">Why:</span>{" "}
                                <span className="font-mono text-text-secondary">
                                  {job.placement_decision.chosen_reason}
                                </span>
                              </div>
                            </div>
                            <table className="w-full text-xs">
                              <thead>
                                <tr className="text-text-muted">
                                  <th className="text-left font-medium px-2 py-1">Node</th>
                                  <th className="text-left font-medium px-2 py-1">Avail/Total GPU</th>
                                  <th className="text-left font-medium px-2 py-1">Util</th>
                                  <th className="text-left font-medium px-2 py-1">Partitions</th>
                                  <th className="text-left font-medium px-2 py-1">State</th>
                                  <th className="text-left font-medium px-2 py-1">Result</th>
                                </tr>
                              </thead>
                              <tbody>
                                {job.placement_decision.candidates.map((cand) => (
                                  <tr
                                    key={cand.node_id}
                                    className={
                                      cand.selected
                                        ? "bg-state-done/10"
                                        : cand.eligible
                                          ? ""
                                          : "text-text-muted"
                                    }
                                  >
                                    <td className="font-mono px-2 py-1">{cand.node_id}</td>
                                    <td className="font-mono px-2 py-1">
                                      {cand.available_gpu}/{cand.gpu_count}
                                    </td>
                                    <td className="font-mono px-2 py-1">
                                      {(cand.avg_utilization * 100).toFixed(0)}%
                                    </td>
                                    <td className="font-mono px-2 py-1">
                                      {cand.partitions.join(",") || "-"}
                                    </td>
                                    <td className="font-mono px-2 py-1">{cand.state || "-"}</td>
                                    <td className="px-2 py-1">
                                      {cand.selected ? (
                                        <span className="text-state-done font-medium">SELECTED</span>
                                      ) : cand.eligible ? (
                                        <span className="text-text-secondary">eligible</span>
                                      ) : (
                                        <span className="text-text-muted">{cand.rejected_reason}</span>
                                      )}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
          </div>
        );
      })()}
    </div>
  );
}
