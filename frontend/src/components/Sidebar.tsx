import React from "react";
import { NavLink } from "react-router-dom";
import { useOperatorAuth } from "../auth-context";

const baseLinks = [
  { to: "/", label: "Dashboard", icon: "grid" },
  { to: "/jobs", label: "Jobs", icon: "play" },
  { to: "/nodes", label: "Nodes", icon: "server" },
  { to: "/request-token", label: "Request Token", icon: "mail" },
];

const icons: Record<string, React.ReactNode> = {
  grid: (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z" />
    </svg>
  ),
  play: (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
      <path d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  ),
  server: (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path d="M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2" />
    </svg>
  ),
  mail: (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path d="M3 8l9 6 9-6" />
      <path d="M21 8v8a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h14a2 2 0 012 2z" />
    </svg>
  ),
  shield: (
    <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path d="M12 3l8 4v5c0 5-3.5 8.5-8 9-4.5-.5-8-4-8-9V7l8-4z" />
    </svg>
  ),
};

export default function Sidebar() {
  const { token, setToken, clearToken, me, meError, loadingMe } = useOperatorAuth();
  const links = [...baseLinks];
  if (me?.role === "admin") {
    links.push({ to: "/admin/token-requests", label: "Token Requests", icon: "shield" });
  }

  return (
    <aside className="w-60 shrink-0 border-r border-border bg-surface-1 flex flex-col">
      <div className="px-5 py-4 border-b border-border">
        <h1 className="text-sm font-bold tracking-wide text-text-primary">CUDA Scheduler</h1>
        <span className="text-xs text-text-muted">Admin Console</span>
      </div>
      <nav className="flex-1 px-3 py-3 space-y-0.5">
        {links.map((l) => (
          <NavLink
            key={l.to}
            to={l.to}
            end={l.to === "/"}
            className={({ isActive }) =>
              `flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors ${
                isActive
                  ? "bg-accent/10 text-accent font-medium"
                  : "text-text-secondary hover:bg-surface-2 hover:text-text-primary"
              }`
            }
          >
            {icons[l.icon]}
            {l.label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-border px-4 py-4 space-y-2">
        <label className="block text-[11px] font-medium uppercase tracking-wide text-text-muted">API Token</label>
        <input
          type="password"
          value={token}
          onChange={(event) => setToken(event.target.value)}
          placeholder="Bearer token"
          className="w-full rounded-md border border-border bg-surface-0 px-3 py-2 text-xs text-text-primary focus:border-accent focus:outline-none"
        />
        <div className="text-[11px] text-text-muted">
          {loadingMe ? "Validating token..." : me ? `Signed as ${me.subject} (${me.role})` : "No active token"}
        </div>
        {meError ? <div className="text-[11px] text-state-failed">{meError}</div> : null}
        <div className="mt-2 flex items-center justify-between text-[11px] text-text-muted">
          <span>{token ? "Stored in session" : "Public pages only"}</span>
          {token ? (
            <button
              type="button"
              onClick={clearToken}
              className="text-accent transition-colors hover:text-accent-hover"
            >
              Clear
            </button>
          ) : null}
        </div>
      </div>
    </aside>
  );
}
