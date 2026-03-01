import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import Card from "../components/Card";
import {
  fetchHealth,
  fetchReady,
  fetchJobSummary,
  type HealthResponse,
  type ReadyResponse,
  type JobSummary,
} from "../api/client";

function HealthDot({ ok }: { ok: boolean | null }) {
  if (ok === null) return <span className="inline-block w-2.5 h-2.5 rounded-full bg-surface-2 animate-pulse" />;
  return (
    <span
      className={`inline-block w-2.5 h-2.5 rounded-full ${ok ? "bg-state-done" : "bg-state-failed"}`}
    />
  );
}

const summaryCards: { key: keyof JobSummary; label: string; color: string }[] = [
  { key: "queued", label: "Queued", color: "text-state-queued" },
  { key: "placed", label: "Placed", color: "text-state-placed" },
  { key: "running", label: "Running", color: "text-state-running" },
  { key: "done", label: "Done", color: "text-state-done" },
  { key: "failed", label: "Failed", color: "text-state-failed" },
  { key: "cancelled", label: "Cancelled", color: "text-state-cancelled" },
];

export default function Dashboard() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [ready, setReady] = useState<ReadyResponse | null>(null);
  const [summary, setSummary] = useState<JobSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const [h, r, s] = await Promise.all([fetchHealth(), fetchReady(), fetchJobSummary()]);
        if (!cancelled) {
          setHealth(h);
          setReady(r);
          setSummary(s);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load");
      }
    }
    load();
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="max-w-5xl space-y-6">
      <h2 className="text-lg font-semibold">Dashboard</h2>

      {error && (
        <div className="rounded-md border border-state-failed/30 bg-state-failed/10 px-4 py-3 text-sm text-state-failed">
          {error}
        </div>
      )}

      {/* Health & readiness */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <Card>
          <div className="flex items-center gap-2 text-sm text-text-secondary mb-1">
            <HealthDot ok={health?.ok ?? null} />
            Control Plane
          </div>
          <p className="text-xs text-text-muted font-mono">
            {health ? (health.ok ? "Healthy" : "Unhealthy") : "Loading..."}
          </p>
        </Card>
        <Card>
          <div className="flex items-center gap-2 text-sm text-text-secondary mb-1">
            <HealthDot ok={ready?.postgres?.ok ?? null} />
            PostgreSQL
          </div>
          <p className="text-xs text-text-muted font-mono">
            {ready?.postgres ? (ready.postgres.ok ? "Connected" : "Down") : "Loading..."}
          </p>
        </Card>
        <Card>
          <div className="flex items-center gap-2 text-sm text-text-secondary mb-1">
            <HealthDot ok={ready?.redis?.ok ?? null} />
            Redis
          </div>
          <p className="text-xs text-text-muted font-mono">
            {ready?.redis ? (ready.redis.ok ? "Connected" : "Down") : "Loading..."}
          </p>
        </Card>
      </div>

      {/* Job summary cards */}
      <div>
        <h3 className="text-sm font-medium text-text-secondary mb-3">Job Summary</h3>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {summaryCards.map((c) => (
            <Card key={c.key} className="text-center">
              <p className={`text-2xl font-bold font-mono ${c.color}`}>
                {summary ? summary[c.key] : "-"}
              </p>
              <p className="text-xs text-text-muted mt-1">{c.label}</p>
            </Card>
          ))}
        </div>
      </div>

      {/* Quick links */}
      <div className="flex gap-3">
        <Link
          to="/jobs"
          className="rounded-md border border-border bg-surface-2 px-4 py-2 text-sm text-text-secondary hover:text-text-primary hover:border-accent/40 transition-colors"
        >
          View Jobs
        </Link>
        <Link
          to="/nodes"
          className="rounded-md border border-border bg-surface-2 px-4 py-2 text-sm text-text-secondary hover:text-text-primary hover:border-accent/40 transition-colors"
        >
          View Nodes
        </Link>
      </div>
    </div>
  );
}
