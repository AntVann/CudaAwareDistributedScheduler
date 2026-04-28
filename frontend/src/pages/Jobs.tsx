import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import Card from "../components/Card";
import StatusBadge from "../components/StatusBadge";
import { useOperatorAuth } from "../auth-context";
import {
  fetchJobs,
  submitJob,
  type JobListItem,
  type EnqueueResponse,
} from "../api/client";

function formatTs(ts: number | null | undefined): string {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleString();
}

function truncate(s: string | null | undefined, max = 60): string {
  if (!s) return "-";
  return s.length > max ? s.slice(0, max) + "..." : s;
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
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Submit form state
  const [showSubmit, setShowSubmit] = useState(false);
  const [project, setProject] = useState("");
  const [cmd, setCmd] = useState('["echo", "hello"]');
  const [gpus, setGpus] = useState(1);
  const [partition, setPartition] = useState("");
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
      const res: EnqueueResponse = await submitJob({
        job_id: jobId,
        project: project.trim(),
        image: "",
        cmd: parsedCmd,
        gpus,
        metadata,
      }, token);
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
    <div className="max-w-6xl space-y-5">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold">Jobs</h2>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-text-secondary cursor-pointer">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="accent-accent"
            />
            Auto-refresh
          </label>
          <button
            onClick={() => setShowSubmit(!showSubmit)}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-hover transition-colors"
          >
            Submit Test Job
          </button>
        </div>
      </div>

      {/* Submit panel */}
      {showSubmit && (
        <Card>
          <h3 className="text-sm font-medium text-text-secondary mb-3">Submit a Test Job</h3>
          <p className="mb-3 text-xs text-text-muted">
            This action requires a valid user/admin token stored in the sidebar.
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

      {/* Jobs table */}
      {loading ? (
        <p className="text-sm text-text-muted">Loading...</p>
      ) : jobs.length === 0 ? (
        <Card>
          <p className="text-sm text-text-muted text-center py-4">No jobs found. Submit a test job to get started.</p>
        </Card>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-surface-1 text-left text-xs text-text-muted">
                <th className="px-4 py-2.5 font-medium">Job ID</th>
                <th className="px-4 py-2.5 font-medium">SLURM ID</th>
                <th className="px-4 py-2.5 font-medium">State</th>
                <th className="px-4 py-2.5 font-medium">Project</th>
                <th className="px-4 py-2.5 font-medium">Node</th>
                <th className="px-4 py-2.5 font-medium">Exit Code</th>
                <th className="px-4 py-2.5 font-medium">Reason</th>
                <th className="px-4 py-2.5 font-medium">Enqueued</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
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
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
