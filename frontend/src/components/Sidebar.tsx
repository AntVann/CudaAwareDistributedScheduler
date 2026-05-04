import { useState } from "react";
import { NavLink } from "react-router-dom";
import { useOperatorAuth } from "../auth-context";
import Icon from "./Icon";

type NavItem = {
  to: string;
  label: string;
  icon: "grid" | "play" | "server" | "mail" | "shield";
  section: "operate" | "access";
  adminOnly?: boolean;
};

const NAV: NavItem[] = [
  { to: "/", label: "Dashboard", icon: "grid", section: "operate" },
  { to: "/jobs", label: "Jobs", icon: "play", section: "operate" },
  { to: "/nodes", label: "Nodes", icon: "server", section: "operate" },
  { to: "/request-token", label: "Request Token", icon: "mail", section: "access" },
  {
    to: "/admin/token-requests",
    label: "Token Requests",
    icon: "shield",
    section: "access",
    adminOnly: true,
  },
];

function NavSection({ label, items }: { label: string; items: NavItem[] }) {
  if (items.length === 0) return null;
  return (
    <>
      <div className="nav-section-label">{label}</div>
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === "/"}
          className={({ isActive }) => "nav-link" + (isActive ? " active" : "")}
        >
          <Icon name={item.icon} />
          <span>{item.label}</span>
        </NavLink>
      ))}
    </>
  );
}

export default function Sidebar() {
  const { token, setToken, clearToken, me, meError, loadingMe } = useOperatorAuth();
  const [draftToken, setDraftToken] = useState("");

  const visible = NAV.filter((item) => !item.adminOnly || me?.role === "admin");
  const operate = visible.filter((i) => i.section === "operate");
  const access = visible.filter((i) => i.section === "access");

  const initial = me?.subject?.[0]?.toUpperCase() ?? "?";

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-row">
          <div className="brand-mark">c</div>
          <div>
            <div className="brand-name">cudaScheduler</div>
            <div className="brand-sub">Admin Console</div>
          </div>
        </div>
      </div>

      <nav className="nav">
        <NavSection label="Operate" items={operate} />
        <NavSection label="Access" items={access} />
      </nav>

      <div className="token-block">
        {token && me ? (
          <div className="token-card signed">
            <div className="token-row">
              <div className="token-avatar">{initial}</div>
              <div className="grow" style={{ minWidth: 0 }}>
                <div className="token-name">{me.subject}</div>
                <div className="token-sub">{me.role} · session</div>
              </div>
              <button
                type="button"
                className="token-clear"
                onClick={clearToken}
                title="Clear token"
                aria-label="Clear token"
              >
                <Icon name="logout" size={14} />
              </button>
            </div>
          </div>
        ) : (
          <div className="token-card">
            <div
              style={{
                fontSize: 11,
                fontWeight: 600,
                color: "var(--color-text-2)",
                letterSpacing: ".05em",
                textTransform: "uppercase",
                marginBottom: 6,
              }}
            >
              API Token
            </div>
            <input
              className="input"
              type="text"
              placeholder="Paste bearer token"
              value={draftToken}
              onChange={(e) => setDraftToken(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && draftToken.trim()) {
                  setToken(draftToken.trim());
                  setDraftToken("");
                }
              }}
              onBlur={() => {
                if (draftToken.trim()) {
                  setToken(draftToken.trim());
                  setDraftToken("");
                }
              }}
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
              spellCheck={false}
              data-1p-ignore="true"
              data-lpignore="true"
              data-bwignore="true"
              data-form-type="other"
              style={{ height: 28, fontSize: 12, fontFamily: "var(--font-mono)" }}
            />
            <div
              style={{
                fontSize: 11,
                color: meError ? "var(--color-danger)" : "var(--color-text-3)",
                marginTop: 6,
              }}
            >
              {loadingMe ? (
                "Validating token…"
              ) : meError ? (
                meError
              ) : (
                <>
                  Stored in session ·{" "}
                  <NavLink
                    to="/request-token"
                    style={{ color: "var(--color-accent)", textDecoration: "none" }}
                  >
                    Request one →
                  </NavLink>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}
