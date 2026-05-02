import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import Icon from "../components/Icon";
import { useOperatorAuth } from "../auth-context";
import {
  fetchHealth,
  fetchJobs,
  fetchMetricsSummary,
  fetchPolicies,
  fetchReady,
  type HealthResponse,
  type JobListItem,
  type MetricsSummary,
  type PoliciesResponse,
  type ReadyResponse,
  updateActivePolicy,
} from "../api/client";

const STATE_TONE: Record<string, string> = {
  QUEUED: "var(--color-text-2)",
  PLACED: "var(--color-info)",
  RUNNING: "var(--color-accent)",
  DONE: "var(--color-ok)",
  FAILED: "var(--color-danger)",
  CANCELLED: "var(--color-text-3)",
};

function fmtRel(seconds: number): string {
  if (seconds < 10) return "just now";
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function readyMode(
  ready: ReadyResponse | null,
  field: "postgres" | "redis",
): "ok" | "down" | "unknown" {
  if (!ready) return "unknown";
  const sub = ready[field];
  if (!sub) return "unknown";
  return sub.ok ? "ok" : "down";
}

function readyLabel(ready: ReadyResponse | null, field: "postgres" | "redis"): string {
  const sub = ready?.[field];
  if (!sub) return field === "postgres" ? "Storage" : "Queue";
  if (field === "postgres") {
    return sub.mode === "sqlite" ? "SQLite" : "PostgreSQL";
  }
  return sub.mode === "memory" ? "Queue" : "Redis";
}

/** Heuristic verdict — counts attention items based on metrics + jobs. */
function buildVerdict(args: {
  health: HealthResponse | null;
  ready: ReadyResponse | null;
  metrics: MetricsSummary | null;
  jobs: JobListItem[];
}): {
  ok: boolean;
  issueCount: number;
  attention: AttentionItem[];
} {
  const items: AttentionItem[] = [];
  const { health, ready, metrics, jobs } = args;

  if (health && !health.ok) {
    items.push({
      tone: "danger",
      icon: "alert",
      title: "Control plane unhealthy",
      meta: undefined,
      body: "The /health probe is reporting failure. Restart uvicorn or check logs.",
    });
  }
  if (ready && !ready.ok) {
    items.push({
      tone: "danger",
      icon: "alert",
      title: "Storage / queue unreachable",
      meta: undefined,
      body: "The control plane could not reach its storage backend. Check DATABASE_URL and uvicorn startup logs.",
    });
  }

  // Stuck jobs: any QUEUED row with a placement_decision blob whose
  // chosen_node_id is null is a job the scheduler couldn't place.
  for (const job of jobs) {
    if (
      job.state === "QUEUED" &&
      job.placement_decision &&
      job.placement_decision.chosen_node_id === null
    ) {
      const why = job.placement_decision.chosen_reason ?? "no eligible nodes";
      items.push({
        tone: "warn",
        icon: "clock",
        title: "Job stuck in queue",
        meta: job.job_id,
        body: why,
        action: "Inspect",
        navigateTo: "/jobs",
      });
    }
  }

  // Stale nodes (metrics summary already aggregates, but only carry it as a
  // single item — the Nodes page is where to drill in).
  if (metrics && metrics.nodes && metrics.nodes.stale > 0) {
    items.push({
      tone: "warn",
      icon: "server",
      title: `${metrics.nodes.stale} node${metrics.nodes.stale === 1 ? "" : "s"} stale`,
      body: "One or more nodes haven't sent a heartbeat recently.",
      action: "View nodes",
      navigateTo: "/nodes",
    });
  }

  return { ok: items.length === 0, issueCount: items.length, attention: items };
}

interface AttentionItem {
  tone: "warn" | "danger" | "info";
  icon: "clock" | "alert" | "server" | "info";
  title: string;
  meta?: string;
  body: string;
  action?: string;
  navigateTo?: string;
}

function VerdictHeader({
  ok,
  issueCount,
  ready,
  health,
  metrics,
  loading,
  lastUpdated,
}: {
  ok: boolean;
  issueCount: number;
  ready: ReadyResponse | null;
  health: HealthResponse | null;
  metrics: MetricsSummary | null;
  loading: boolean;
  lastUpdated: number;
}) {
  const cpMode = health?.ok ? "ok" : health ? "down" : "unknown";
  const storageMode = readyMode(ready, "postgres");
  const queueMode = readyMode(ready, "redis");
  const sub = (() => {
    if (loading || !metrics) return "Loading cluster snapshot…";
    const total = metrics.nodes.total;
    const running = metrics.jobs.running;
    const updated = fmtRel(Math.floor((Date.now() - lastUpdated) / 1000));
    return `${total} node${total === 1 ? "" : "s"} · ${running} job${running === 1 ? "" : "s"} running · ${updated}`;
  })();

  return (
    <div
      className="card"
      style={{
        padding: "20px 24px",
        marginBottom: 20,
        background: ok ? "var(--color-ok-soft)" : "var(--color-surface)",
        borderColor: ok ? "var(--color-ok-border)" : "var(--color-border)",
      }}
    >
      <div className="flex items-center gap-4">
        <div
          style={{
            width: 44,
            height: 44,
            borderRadius: 999,
            background: ok ? "var(--color-ok)" : "var(--color-warn-soft)",
            color: ok ? "white" : "var(--color-warn)",
            display: "grid",
            placeItems: "center",
            border: ok ? "none" : "1px solid var(--color-warn-border)",
            flexShrink: 0,
          }}
        >
          <Icon name={ok ? "check" : "alert"} size={22} />
        </div>
        <div className="grow">
          <div
            style={{
              fontSize: 11,
              fontWeight: 600,
              color: "var(--color-text-3)",
              letterSpacing: ".08em",
              textTransform: "uppercase",
            }}
          >
            Cluster status
          </div>
          <div className="text-xl font-semibold" style={{ marginTop: 2 }}>
            {ok ? "All systems operational." : `${issueCount} item${issueCount === 1 ? "" : "s"} need attention`}
          </div>
          <div className="text-sm muted" style={{ marginTop: 2 }}>
            {sub}
          </div>
        </div>
        <div className="flex gap-2" style={{ flexWrap: "wrap" }}>
          <ServicePill mode={cpMode} label="Control Plane" />
          <ServicePill mode={storageMode} label={readyLabel(ready, "postgres")} />
          <ServicePill mode={queueMode} label={readyLabel(ready, "redis")} />
        </div>
      </div>
    </div>
  );
}

function ServicePill({ mode, label }: { mode: "ok" | "down" | "unknown"; label: string }) {
  const dotBg =
    mode === "ok"
      ? "var(--color-ok)"
      : mode === "down"
        ? "var(--color-danger)"
        : "var(--color-text-3)";
  return (
    <span className="pill">
      <span className="pill-dot" style={{ background: dotBg }} />
      {label}
    </span>
  );
}

function KpiTile({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string | number;
  sub?: string;
  accent?: string;
}) {
  return (
    <div className="card card-pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div
        className="muted font-medium"
        style={{ textTransform: "uppercase", letterSpacing: ".06em", fontSize: 10.5 }}
      >
        {label}
      </div>
      <div>
        <div
          className="num text-3xl"
          style={{
            lineHeight: 1,
            fontWeight: 600,
            color: accent ?? "var(--color-text)",
          }}
        >
          {value}
        </div>
        {sub && <div className="text-xs muted mt-1">{sub}</div>}
      </div>
    </div>
  );
}

function AttentionRow({ item, onAction }: { item: AttentionItem; onAction: () => void }) {
  const toneCls = item.tone === "warn" ? "warn" : item.tone === "danger" ? "danger" : "info";
  return (
    <div
      style={{
        padding: "14px 18px",
        borderBottom: "1px solid var(--color-border)",
        display: "flex",
        alignItems: "flex-start",
        gap: 12,
      }}
    >
      <div
        className={"pill " + toneCls}
        style={{ width: 28, height: 28, padding: 0, justifyContent: "center", borderRadius: 6 }}
      >
        <Icon name={item.icon} size={14} />
      </div>
      <div className="grow" style={{ minWidth: 0 }}>
        <div className="flex items-center gap-2" style={{ flexWrap: "wrap" }}>
          <div className="font-semibold text-sm">{item.title}</div>
          {item.meta && <div className="text-xs muted mono">{item.meta}</div>}
        </div>
        <div className="text-sm muted mt-1">{item.body}</div>
      </div>
      {item.action && (
        <button type="button" className="btn btn-sm" onClick={onAction}>
          {item.action} <Icon name="chevron-right" size={12} />
        </button>
      )}
    </div>
  );
}

function CurrentJobsStrip({
  jobs,
  onOpen,
}: {
  jobs: MetricsSummary["jobs"] | undefined;
  onOpen: () => void;
}) {
  const rows: { state: keyof MetricsSummary["jobs"]; label: string }[] = [
    { state: "queued", label: "QUEUED" },
    { state: "placed", label: "PLACED" },
    { state: "running", label: "RUNNING" },
    { state: "done", label: "DONE" },
    { state: "failed", label: "FAILED" },
    { state: "cancelled", label: "CANCELLED" },
  ];
  return (
    <div className="card">
      <div className="card-head">
        <div className="card-title">Current jobs</div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onOpen}>
          Open Jobs <Icon name="chevron-right" size={12} />
        </button>
      </div>
      <div style={{ padding: 0 }}>
        {rows.map((row, i) => (
          <div
            key={row.state}
            style={{
              display: "flex",
              alignItems: "center",
              padding: "11px 18px",
              borderBottom: i < rows.length - 1 ? "1px solid var(--color-border)" : 0,
            }}
          >
            <div className="flex items-center gap-2 grow">
              <span
                className="pill-dot"
                style={{ background: STATE_TONE[row.label], width: 7, height: 7 }}
              />
              <span className="text-sm font-medium">{row.label}</span>
            </div>
            <div
              className="num font-semibold text-md"
              style={{ color: STATE_TONE[row.label] }}
            >
              {jobs ? jobs[row.state] : "—"}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function PolicyButton({
  value,
  active,
  disabled,
  onClick,
  title,
}: {
  value: string;
  active: boolean;
  disabled: boolean;
  onClick: () => void;
  title?: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={"chip" + (active ? " active" : "")}
      style={{ height: 32, padding: "0 14px", fontSize: 13 }}
    >
      {value}
      {active && <Icon name="check" size={13} />}
    </button>
  );
}

export default function Dashboard() {
  const { token } = useOperatorAuth();
  const navigate = useNavigate();

  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [ready, setReady] = useState<ReadyResponse | null>(null);
  const [metrics, setMetrics] = useState<MetricsSummary | null>(null);
  const [policies, setPolicies] = useState<PoliciesResponse | null>(null);
  const [jobs, setJobs] = useState<JobListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [policyStatus, setPolicyStatus] = useState<string | null>(null);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const [updatingPolicy, setUpdatingPolicy] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<number>(Date.now());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [healthResponse, readyResponse] = await Promise.all([
          fetchHealth(),
          fetchReady(),
        ]);
        let metricsResponse: MetricsSummary | null = null;
        let policiesResponse: PoliciesResponse | null = null;
        let jobsResponse: JobListItem[] = [];
        if (token) {
          [metricsResponse, policiesResponse, jobsResponse] = await Promise.all([
            fetchMetricsSummary(60, token),
            fetchPolicies(token),
            fetchJobs(token),
          ]);
        }
        if (!cancelled) {
          setHealth(healthResponse);
          setReady(readyResponse);
          setMetrics(metricsResponse);
          setPolicies(policiesResponse);
          setJobs(jobsResponse);
          setLastUpdated(Date.now());
          setError(null);
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load dashboard");
          setLoading(false);
        }
      }
    }

    load();
    const timer = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [token]);

  async function handlePolicyChange(nextPolicy: string) {
    if (!token) {
      setPolicyError("Enter an admin token to change the active policy.");
      setPolicyStatus(null);
      return;
    }
    setUpdatingPolicy(true);
    setPolicyError(null);
    setPolicyStatus(null);
    try {
      const updated = await updateActivePolicy(nextPolicy, token);
      setPolicies(updated);
      setPolicyStatus(`Active policy changed to ${updated.active}.`);
    } catch (err) {
      setPolicyError(err instanceof Error ? err.message : "Failed to update policy");
    } finally {
      setUpdatingPolicy(false);
    }
  }

  const verdict = useMemo(
    () => buildVerdict({ health, ready, metrics, jobs }),
    [health, ready, metrics, jobs],
  );

  const queueDepth = metrics?.queue_depth ?? 0;
  const placementP95 = metrics?.latency_ms.placement_p95 ?? 0;
  const runP50 = metrics?.latency_ms.run_p50 ?? 0;
  const runP95 = metrics?.latency_ms.run_p95 ?? 0;
  const doneRecent = metrics?.windowed_terminal_counts.done ?? 0;
  const failedRecent = metrics?.windowed_terminal_counts.failed ?? 0;

  return (
    <div className="page fade-in">
      <div className="page-header">
        <div>
          <h1 className="page-title">Dashboard</h1>
          <div className="page-sub">
            Operational overview of the cudaScheduler control plane.
          </div>
        </div>
        <div className="page-actions">
          <span className="text-xs muted">
            Updated {fmtRel(Math.floor((Date.now() - lastUpdated) / 1000))}
          </span>
        </div>
      </div>

      {error && (
        <div
          className="card mb-4"
          style={{
            padding: "12px 16px",
            borderColor: "var(--color-danger-border)",
            background: "var(--color-danger-soft)",
            color: "var(--color-danger)",
          }}
        >
          {error}
        </div>
      )}

      <VerdictHeader
        ok={verdict.ok}
        issueCount={verdict.issueCount}
        ready={ready}
        health={health}
        metrics={metrics}
        loading={loading}
        lastUpdated={lastUpdated}
      />

      {/* Hero KPI strip — 4 tiles */}
      <div className="grid grid-cols-4 gap-4 mb-4">
        <KpiTile
          label="Queue depth"
          value={queueDepth}
          sub="pending placement"
          accent="var(--color-accent)"
        />
        <KpiTile
          label="Placement p95"
          value={placementP95}
          sub="ms · last 60m"
          accent={placementP95 > 1000 ? "var(--color-warn)" : undefined}
        />
        <KpiTile
          label="Run latency p50 / p95"
          value={`${runP50} / ${runP95}`}
          sub="ms · last 60m"
        />
        <KpiTile
          label="Recent terminal"
          value={`${doneRecent} ✓ ${failedRecent} ✗`}
          sub="done / failed · last 60m"
          accent={failedRecent > 0 ? "var(--color-danger)" : "var(--color-ok)"}
        />
      </div>

      {/* Attention feed + current jobs */}
      <div
        className="grid gap-4"
        style={{ gridTemplateColumns: "1.3fr 1fr" }}
      >
        <div className="card">
          <div className="card-head">
            <div className="flex items-center gap-2">
              <div className="card-title">Needs attention</div>
              {verdict.issueCount > 0 ? (
                <span className="pill warn">
                  <span
                    className="pill-dot"
                    style={{ background: "var(--color-warn)" }}
                  />
                  {verdict.issueCount} item{verdict.issueCount === 1 ? "" : "s"}
                </span>
              ) : (
                <span className="pill ok">
                  <span
                    className="pill-dot"
                    style={{ background: "var(--color-ok)" }}
                  />
                  clear
                </span>
              )}
            </div>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => navigate("/jobs")}
            >
              View all
            </button>
          </div>
          {verdict.attention.length === 0 ? (
            <div
              className="empty"
              style={{ padding: "32px 20px", color: "var(--color-text-2)" }}
            >
              <div className="empty-icon">
                <Icon name="check" size={28} />
              </div>
              <div className="font-semibold">Nothing needs your attention.</div>
              <div className="text-sm mt-1">
                All nodes fresh, no stuck jobs, services healthy.
              </div>
            </div>
          ) : (
            <div>
              {verdict.attention.map((item, idx) => (
                <AttentionRow
                  key={idx}
                  item={item}
                  onAction={() => item.navigateTo && navigate(item.navigateTo)}
                />
              ))}
            </div>
          )}
        </div>

        <CurrentJobsStrip jobs={metrics?.jobs} onOpen={() => navigate("/jobs")} />
      </div>

      {/* Latency + policy */}
      <div
        className="grid gap-4 mt-4"
        style={{ gridTemplateColumns: "1.3fr 1fr" }}
      >
        <div className="card">
          <div className="card-head">
            <div>
              <div className="card-title">Placement latency</div>
              <div className="text-xs muted mt-1">
                Time from enqueue → placed, last 60 minutes
              </div>
            </div>
            <div className="flex gap-3 text-xs muted">
              <span className="flex items-center gap-1">
                <span
                  className="pill-dot"
                  style={{ background: "var(--color-accent)", width: 6, height: 6 }}
                />
                p50 <span className="num">{metrics?.latency_ms.placement_p50 ?? 0}ms</span>
              </span>
              <span className="flex items-center gap-1">
                <span
                  className="pill-dot"
                  style={{ background: "var(--color-warn)", width: 6, height: 6 }}
                />
                p95 <span className="num">{placementP95}ms</span>
              </span>
            </div>
          </div>
          <div className="card-body">
            <div className="text-sm muted">
              Live time-series isn't wired through the metrics endpoint yet — the
              tiles above show the rolling p50/p95 directly. A full chart can land
              alongside a windowed metrics endpoint.
            </div>
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <div className="card-title">Scheduling policy</div>
            <span className="pill accent mono">{policies?.active ?? "—"}</span>
          </div>
          <div className="card-body">
            <div className="text-sm muted mb-3">
              Determines how queued jobs are assigned to eligible nodes. Changes
              take effect immediately.
            </div>
            <div className="flex gap-2" style={{ flexWrap: "wrap" }}>
              {(policies?.supported ?? ["FIFO", "ROUND_ROBIN", "BINPACK"]).map((p) => (
                <PolicyButton
                  key={p}
                  value={p}
                  active={policies?.active === p}
                  disabled={
                    !token ||
                    updatingPolicy ||
                    !policies ||
                    policies.active === p
                  }
                  onClick={() => handlePolicyChange(p)}
                  title={
                    !token
                      ? "Read-only — paste an admin token to change the policy"
                      : policies?.active === p
                        ? "Already active"
                        : undefined
                  }
                />
              ))}
            </div>
            {!token && (
              <div className="text-xs mt-3" style={{ color: "var(--color-warn)" }}>
                Read-only — admin token required to change policy.
              </div>
            )}
            {policyStatus && (
              <div
                className="text-xs mt-3"
                style={{ color: "var(--color-ok)" }}
              >
                {policyStatus}
              </div>
            )}
            {policyError && (
              <div
                className="text-xs mt-3"
                style={{ color: "var(--color-danger)" }}
              >
                {policyError}
              </div>
            )}
            <hr className="hr mt-4 mb-3" style={{ border: 0, height: 1, background: "var(--color-border)" }} />
            <div className="grid grid-cols-2 gap-3">
              <div>
                <div className="text-xs muted">Run latency p50</div>
                <div className="num font-semibold text-lg mt-1">{runP50}ms</div>
              </div>
              <div>
                <div className="text-xs muted">Run latency p95</div>
                <div className="num font-semibold text-lg mt-1">{runP95}ms</div>
              </div>
              <div>
                <div className="text-xs muted">Done · last 60m</div>
                <div
                  className="num font-semibold text-lg mt-1"
                  style={{ color: "var(--color-ok)" }}
                >
                  {doneRecent}
                </div>
              </div>
              <div>
                <div className="text-xs muted">Failed · last 60m</div>
                <div
                  className="num font-semibold text-lg mt-1"
                  style={{
                    color: failedRecent > 0 ? "var(--color-danger)" : "var(--color-text-2)",
                  }}
                >
                  {failedRecent}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
