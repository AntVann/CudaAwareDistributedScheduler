// Maps a JobState to a `pill` variant from the design system.
// The new palette is restrained: neutral / info / accent / ok / danger.
const STATE_PILL_VARIANT: Record<string, string> = {
  QUEUED: "pill",
  PLACED: "pill info",
  RUNNING: "pill accent",
  DONE: "pill ok",
  FAILED: "pill danger",
  CANCELLED: "pill",
};

export default function StatusBadge({ state }: { state: string }) {
  const cls = STATE_PILL_VARIANT[state] ?? "pill";
  return (
    <span className={`${cls} mono`}>
      <span className="pill-dot" style={{ width: 5, height: 5 }} />
      {state}
    </span>
  );
}
