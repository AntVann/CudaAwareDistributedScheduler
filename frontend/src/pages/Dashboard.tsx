import { useEffect, useState } from "react";
import Card from "../components/Card";
import { useOperatorAuth } from "../auth-context";
import {
  fetchHealth,
  fetchMetricsSummary,
  fetchPolicies,
  fetchReady,
  type HealthResponse,
  type MetricsSummary,
  type PoliciesResponse,
  type ReadyResponse,
  updateActivePolicy,
} from "../api/client";

function HealthDot({ ok }: { ok: boolean | null }) {
  if (ok === null) {
    return <span className="inline-block h-2.5 w-2.5 animate-pulse rounded-full bg-surface-2" />;
  }
  return (
    <span
      className={`inline-block h-2.5 w-2.5 rounded-full ${ok ? "bg-state-done" : "bg-state-failed"}`}
    />
  );
}

function MetricValue({ value }: { value: number }) {
  return <span className="font-mono text-2xl font-bold text-text-primary">{value}</span>;
}

export default function Dashboard() {
  const { token } = useOperatorAuth();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [ready, setReady] = useState<ReadyResponse | null>(null);
  const [metrics, setMetrics] = useState<MetricsSummary | null>(null);
  const [policies, setPolicies] = useState<PoliciesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [policyStatus, setPolicyStatus] = useState<string | null>(null);
  const [policyError, setPolicyError] = useState<string | null>(null);
  const [updatingPolicy, setUpdatingPolicy] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const [healthResponse, readyResponse, metricsResponse, policiesResponse] = await Promise.all([
          fetchHealth(),
          fetchReady(),
          fetchMetricsSummary(),
          fetchPolicies(),
        ]);
        if (!cancelled) {
          setHealth(healthResponse);
          setReady(readyResponse);
          setMetrics(metricsResponse);
          setPolicies(policiesResponse);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load dashboard");
        }
      }
    }

    load();
    const timer = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  async function handlePolicyChange(nextPolicy: string) {
    if (!token) {
      setPolicyError("Enter the operator token to change the active policy.");
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

  const summary = metrics?.jobs;

  return (
    <div className="max-w-6xl space-y-6">
      <h2 className="text-lg font-semibold">Dashboard</h2>

      {error ? (
        <div className="rounded-md border border-state-failed/30 bg-state-failed/10 px-4 py-3 text-sm text-state-failed">
          {error}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card>
          <div className="mb-1 flex items-center gap-2 text-sm text-text-secondary">
            <HealthDot ok={health?.ok ?? null} />
            Control Plane
          </div>
          <p className="font-mono text-xs text-text-muted">
            {health ? (health.ok ? "Healthy" : "Unhealthy") : "Loading..."}
          </p>
        </Card>
        <Card>
          <div className="mb-1 flex items-center gap-2 text-sm text-text-secondary">
            <HealthDot ok={ready?.postgres?.ok ?? null} />
            PostgreSQL
          </div>
          <p className="font-mono text-xs text-text-muted">
            {ready?.postgres ? (ready.postgres.ok ? "Connected" : "Down") : "Loading..."}
          </p>
        </Card>
        <Card>
          <div className="mb-1 flex items-center gap-2 text-sm text-text-secondary">
            <HealthDot ok={ready?.redis?.ok ?? null} />
            Redis
          </div>
          <p className="font-mono text-xs text-text-muted">
            {ready?.redis ? (ready.redis.ok ? "Connected" : "Down") : "Loading..."}
          </p>
        </Card>
      </div>

      <div>
        <h3 className="mb-3 text-sm font-medium text-text-secondary">Current Jobs</h3>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          {[
            ["queued", "Queued", "text-state-queued"],
            ["placed", "Placed", "text-state-placed"],
            ["running", "Running", "text-state-running"],
            ["done", "Done", "text-state-done"],
            ["failed", "Failed", "text-state-failed"],
            ["cancelled", "Cancelled", "text-state-cancelled"],
          ].map(([key, label, color]) => (
            <Card key={key} className="text-center">
              <p className={`font-mono text-2xl font-bold ${color}`}>
                {summary ? summary[key as keyof typeof summary] : "-"}
              </p>
              <p className="mt-1 text-xs text-text-muted">{label}</p>
            </Card>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card>
          <p className="mb-1 text-sm text-text-secondary">Queue Depth</p>
          <MetricValue value={metrics?.queue_depth ?? 0} />
          <p className="mt-2 text-xs text-text-muted">
            Based on Redis `jobs:queue` length.
          </p>
        </Card>
        <Card>
          <p className="mb-1 text-sm text-text-secondary">Node Freshness</p>
          <div className="space-y-1 text-sm text-text-secondary">
            <div>
              Total: <span className="font-mono text-text-primary">{metrics?.nodes.total ?? 0}</span>
            </div>
            <div>
              Fresh: <span className="font-mono text-state-done">{metrics?.nodes.fresh ?? 0}</span>
            </div>
            <div>
              Stale: <span className="font-mono text-state-failed">{metrics?.nodes.stale ?? 0}</span>
            </div>
          </div>
        </Card>
        <Card>
          <p className="mb-1 text-sm text-text-secondary">Recent Terminal Outcomes</p>
          <div className="space-y-1 text-sm text-text-secondary">
            <div>
              Done:{" "}
              <span className="font-mono text-state-done">
                {metrics?.windowed_terminal_counts.done ?? 0}
              </span>
            </div>
            <div>
              Failed:{" "}
              <span className="font-mono text-state-failed">
                {metrics?.windowed_terminal_counts.failed ?? 0}
              </span>
            </div>
            <div className="text-xs text-text-muted">
              Window: last {metrics?.window_minutes ?? 60} minutes
            </div>
          </div>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <p className="mb-3 text-sm text-text-secondary">Latency Summary (ms)</p>
          <div className="grid grid-cols-2 gap-3 text-sm text-text-secondary">
            <div className="rounded-md border border-border bg-surface-0 p-3">
              <div>Placement P50</div>
              <div className="mt-1 font-mono text-lg text-text-primary">
                {metrics?.latency_ms.placement_p50 ?? 0}
              </div>
            </div>
            <div className="rounded-md border border-border bg-surface-0 p-3">
              <div>Placement P95</div>
              <div className="mt-1 font-mono text-lg text-text-primary">
                {metrics?.latency_ms.placement_p95 ?? 0}
              </div>
            </div>
            <div className="rounded-md border border-border bg-surface-0 p-3">
              <div>Run P50</div>
              <div className="mt-1 font-mono text-lg text-text-primary">
                {metrics?.latency_ms.run_p50 ?? 0}
              </div>
            </div>
            <div className="rounded-md border border-border bg-surface-0 p-3">
              <div>Run P95</div>
              <div className="mt-1 font-mono text-lg text-text-primary">
                {metrics?.latency_ms.run_p95 ?? 0}
              </div>
            </div>
          </div>
        </Card>

        <Card>
          <div className="mb-3 flex items-center justify-between">
            <div>
              <p className="text-sm text-text-secondary">Scheduling Policy</p>
              <p className="text-xs text-text-muted">
                Active: <span className="font-mono text-text-primary">{policies?.active ?? "Loading..."}</span>
              </p>
            </div>
            <span className="text-xs text-text-muted">{token ? "Operator token set" : "Read-only mode"}</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {policies?.supported.map((policy) => (
              <button
                key={policy}
                type="button"
                onClick={() => handlePolicyChange(policy)}
                disabled={updatingPolicy || policies.active === policy}
                className={`rounded-md border px-3 py-2 text-xs font-medium transition-colors ${
                  policies.active === policy
                    ? "border-accent bg-accent/10 text-accent"
                    : "border-border bg-surface-0 text-text-secondary hover:border-accent/40 hover:text-text-primary"
                } disabled:cursor-not-allowed disabled:opacity-60`}
              >
                {policy}
              </button>
            )) ?? null}
          </div>
          {policyStatus ? (
            <p className="mt-3 text-xs text-state-done">{policyStatus}</p>
          ) : null}
          {policyError ? (
            <p className="mt-3 text-xs text-state-failed">{policyError}</p>
          ) : null}
        </Card>
      </div>
    </div>
  );
}
