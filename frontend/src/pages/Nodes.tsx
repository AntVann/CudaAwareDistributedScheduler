import { useEffect, useState } from "react";
import Card from "../components/Card";
import { fetchNodes, type NodeInfo, type GpuInfo } from "../api/client";

function relativeTime(epochSeconds: number | null): string {
  if (!epochSeconds) return "never";
  const diff = Math.floor(Date.now() / 1000 - epochSeconds);
  if (diff < 0) return "just now";
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

function freshness(epochSeconds: number | null): string {
  if (!epochSeconds) return "bg-state-failed";
  const diff = Date.now() / 1000 - epochSeconds;
  if (diff < 30) return "bg-state-done";
  if (diff < 120) return "bg-state-running";
  return "bg-state-failed";
}

function nodeStateBadge(state: string | undefined): { label: string; cls: string } {
  if (!state) return { label: "unknown", cls: "bg-surface-2 text-text-muted" };
  const s = state.toLowerCase();
  if (s === "idle") return { label: "idle", cls: "bg-state-done/15 text-state-done" };
  if (s === "mixed" || s === "mix") return { label: "mixed", cls: "bg-state-running/15 text-state-running" };
  if (s.startsWith("alloc")) return { label: "allocated", cls: "bg-accent/15 text-accent" };
  if (s.startsWith("drain")) return { label: "draining", cls: "bg-state-failed/15 text-state-failed" };
  if (s === "down" || s === "down*") return { label: "down", cls: "bg-state-failed/15 text-state-failed" };
  return { label: state, cls: "bg-surface-2 text-text-muted" };
}

function GpuCard({ gpu }: { gpu: GpuInfo }) {
  const memPct = gpu.mem_total_mb > 0 ? ((gpu.mem_used_mb / gpu.mem_total_mb) * 100).toFixed(0) : 0;
  return (
    <div className="rounded-md border border-border bg-surface-0 p-3 text-xs">
      <div className="flex items-center justify-between mb-2">
        <span className="font-mono text-text-secondary">GPU {gpu.index}</span>
        <span className="text-text-muted">{gpu.name}</span>
      </div>
      <div className="grid grid-cols-2 gap-2 text-text-muted">
        <div>
          Util: <span className="font-mono text-text-secondary">{gpu.utilization.toFixed(0)}%</span>
        </div>
        <div>
          Temp:{" "}
          <span className="font-mono text-text-secondary">
            {gpu.temperature != null ? `${gpu.temperature}\u00B0C` : "-"}
          </span>
        </div>
        <div className="col-span-2">
          Mem:{" "}
          <span className="font-mono text-text-secondary">
            {gpu.mem_used_mb} / {gpu.mem_total_mb} MB ({memPct}%)
          </span>
        </div>
        {/* Memory bar */}
        <div className="col-span-2">
          <div className="w-full h-1.5 bg-surface-2 rounded-full overflow-hidden">
            <div
              className="h-full bg-accent rounded-full transition-all"
              style={{ width: `${memPct}%` }}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

export default function Nodes() {
  const [nodes, setNodes] = useState<NodeInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await fetchNodes();
        if (!cancelled) {
          setNodes(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load nodes");
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    const timer = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  return (
    <div className="max-w-5xl space-y-5">
      <h2 className="text-lg font-semibold">Nodes</h2>

      {error && (
        <div className="rounded-md border border-state-failed/30 bg-state-failed/10 px-4 py-3 text-sm text-state-failed">
          {error}
        </div>
      )}

      {loading ? (
        <p className="text-sm text-text-muted">Loading...</p>
      ) : nodes.length === 0 ? (
        <Card>
          <p className="text-sm text-text-muted text-center py-4">No nodes registered yet.</p>
        </Card>
      ) : (
        <div className="space-y-4">
          {nodes.map((node) => {
            const stateLabel = node.labels?.state;
            const partitionLabel = node.labels?.partition;
            const badge = nodeStateBadge(stateLabel);

            return (
              <Card key={node.node_id}>
                <div className="flex items-center gap-3 mb-4">
                  <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${freshness(node.last_seen)}`} />
                  <h3 className="font-mono text-sm font-medium text-text-primary">{node.node_id}</h3>

                  {/* Node state badge */}
                  {stateLabel && (
                    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${badge.cls}`}>
                      {badge.label}
                    </span>
                  )}

                  <span className="text-xs text-text-muted ml-auto">
                    Last heartbeat: {relativeTime(node.last_seen)}
                  </span>
                </div>

                {/* Partition & labels */}
                {partitionLabel && (
                  <div className="flex flex-wrap gap-2 mb-3">
                    {partitionLabel.split(",").map((p) => (
                      <span
                        key={p}
                        className="rounded-md bg-surface-2 px-2 py-0.5 text-xs font-mono text-text-secondary"
                      >
                        {p.trim()}
                      </span>
                    ))}
                  </div>
                )}

                {/* GPU inventory */}
                {(() => {
                  const hasRealMetrics = node.gpus.length > 0 &&
                    node.gpus.some((g) => g.name !== "unknown" || g.mem_total_mb > 0 || g.utilization > 0);

                  if (node.gpus.length > 0 && hasRealMetrics) {
                    return (
                      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 mb-3">
                        {node.gpus.map((gpu) => (
                          <GpuCard key={gpu.index} gpu={gpu} />
                        ))}
                      </div>
                    );
                  }

                  if (node.gpus.length > 0) {
                    return (
                      <p className="text-xs text-text-muted mb-3">
                        {node.gpus.length} GPU{node.gpus.length !== 1 ? "s" : ""} available (detailed metrics not reported)
                      </p>
                    );
                  }

                  if (stateLabel) {
                    return <p className="text-xs text-text-muted mb-3">No GPU details available</p>;
                  }

                  return null;
                })()}

                {/* Agent health */}
                {Object.keys(node.agent_health).length > 0 && (
                  <div className="border-t border-border pt-3 mt-3">
                    <p className="text-xs text-text-muted mb-1">Agent Health</p>
                    <div className="flex flex-wrap gap-3">
                      {Object.entries(node.agent_health).map(([key, val]) => (
                        <span key={key} className="text-xs font-mono text-text-secondary">
                          {key}: {typeof val === "number" ? val.toFixed(2) : String(val)}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
