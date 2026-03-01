const stateColors: Record<string, string> = {
  QUEUED: "bg-state-queued/20 text-state-queued",
  PLACED: "bg-state-placed/20 text-state-placed",
  RUNNING: "bg-state-running/20 text-state-running",
  DONE: "bg-state-done/20 text-state-done",
  FAILED: "bg-state-failed/20 text-state-failed",
  CANCELLED: "bg-state-cancelled/20 text-state-cancelled",
};

export default function StatusBadge({ state }: { state: string }) {
  const cls = stateColors[state] ?? "bg-surface-2 text-text-secondary";
  return (
    <span className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-semibold font-mono ${cls}`}>
      {state}
    </span>
  );
}
