import { useEffect, useState } from "react";

import Card from "../components/Card";
import {
  approveTokenRequest,
  fetchTokenRequests,
  fetchTokens,
  rejectTokenRequest,
  revokeToken,
  type TokenInfo,
  type TokenRequestItem,
} from "../api/client";
import { useOperatorAuth } from "../auth-context";

export default function AdminTokenRequests() {
  const { token, me } = useOperatorAuth();
  const [requests, setRequests] = useState<TokenRequestItem[]>([]);
  const [tokens, setTokens] = useState<TokenInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [issuedToken, setIssuedToken] = useState<{
    requestId: string;
    token: string;
    expiresAt: string;
  } | null>(null);
  const [copyMessage, setCopyMessage] = useState<string | null>(null);

  async function load() {
    if (!token) {
      setError("Admin token required.");
      setLoading(false);
      return;
    }
    try {
      const [requestRows, tokenRows] = await Promise.all([
        fetchTokenRequests(token, "PENDING"),
        fetchTokens(token),
      ]);
      setRequests(requestRows);
      setTokens(tokenRows);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load admin token data");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  if (!token || !me || me.role !== "admin") {
    return (
      <div className="max-w-3xl space-y-4">
        <h2 className="text-lg font-semibold">Token Requests</h2>
        <Card>
          <p className="text-sm text-text-secondary">Admin token required to access this page.</p>
        </Card>
      </div>
    );
  }

  async function onApprove(requestId: string) {
    setMessage(null);
    setError(null);
    setCopyMessage(null);
    try {
      const response = await approveTokenRequest(requestId, token);
      if (response.plaintext_token) {
        setIssuedToken({
          requestId: response.request_id,
          token: response.plaintext_token,
          expiresAt: response.expires_at,
        });
        setMessage(`Approved request ${response.request_id}. Token returned below because response delivery mode is enabled.`);
      } else {
        setIssuedToken(null);
        setMessage(`Approved request ${response.request_id}. Token emailed to requester.`);
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to approve request");
    }
  }

  async function onReject(requestId: string) {
    setMessage(null);
    setError(null);
    try {
      const response = await rejectTokenRequest(requestId, token, "Rejected by admin.");
      setMessage(`Rejected request ${response.request_id}.`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reject request");
    }
  }

  async function onRevoke(tokenId: string) {
    setMessage(null);
    setError(null);
    try {
      await revokeToken(tokenId, token, "Revoked by admin");
      setMessage(`Revoked token ${tokenId}.`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to revoke token");
    }
  }

  async function onCopyIssuedToken() {
    if (!issuedToken) return;
    try {
      await navigator.clipboard.writeText(issuedToken.token);
      setCopyMessage("Token copied to clipboard.");
    } catch {
      setCopyMessage("Copy failed. Select and copy the token manually.");
    }
  }

  return (
    <div className="max-w-6xl space-y-6">
      <h2 className="text-lg font-semibold">Token Requests</h2>
      {error ? (
        <div className="rounded-md border border-state-failed/30 bg-state-failed/10 px-4 py-3 text-sm text-state-failed">
          {error}
        </div>
      ) : null}
      {message ? (
        <div className="rounded-md border border-state-done/30 bg-state-done/10 px-4 py-3 text-sm text-state-done">
          {message}
        </div>
      ) : null}
      {issuedToken ? (
        <Card>
          <div className="space-y-3">
            <div>
              <h3 className="text-sm font-medium text-text-secondary">Issued Plaintext Token</h3>
              <p className="mt-1 text-xs text-text-muted">
                This token is only shown once in response delivery mode. Copy it now and give it to the requester securely.
              </p>
            </div>
            <div className="rounded-md border border-border bg-surface-0 p-3">
              <div className="mb-2 text-[11px] uppercase tracking-wide text-text-muted">
                Request {issuedToken.requestId}
              </div>
              <div className="break-all font-mono text-sm text-text-primary">{issuedToken.token}</div>
              <div className="mt-2 text-xs text-text-muted">
                Expires: {new Date(issuedToken.expiresAt).toLocaleString()}
              </div>
            </div>
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={onCopyIssuedToken}
                className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-white"
              >
                Copy token
              </button>
              <button
                type="button"
                onClick={() => {
                  setIssuedToken(null);
                  setCopyMessage(null);
                }}
                className="rounded-md border border-border px-3 py-1.5 text-xs font-medium text-text-secondary"
              >
                Dismiss
              </button>
              {copyMessage ? <span className="text-xs text-text-muted">{copyMessage}</span> : null}
            </div>
          </div>
        </Card>
      ) : null}

      <Card>
        <h3 className="mb-3 text-sm font-medium text-text-secondary">Pending Requests</h3>
        {loading ? (
          <p className="text-sm text-text-muted">Loading...</p>
        ) : requests.length === 0 ? (
          <p className="text-sm text-text-muted">No pending token requests.</p>
        ) : (
          <div className="space-y-3">
            {requests.map((request) => (
              <div key={request.request_id} className="rounded-md border border-border bg-surface-0 p-3 text-sm">
                <div className="font-medium text-text-primary">{request.subject_name}</div>
                <div className="text-xs text-text-muted">{request.email}</div>
                <div className="mt-1 text-xs text-text-muted">
                  Projects: <span className="font-mono">{request.requested_projects.join(", ")}</span>
                </div>
                <div className="mt-1 text-xs text-text-muted">Purpose: {request.purpose}</div>
                <div className="mt-3 flex gap-2">
                  <button
                    type="button"
                    onClick={() => onApprove(request.request_id)}
                    className="rounded-md bg-state-done px-3 py-1.5 text-xs font-medium text-white"
                  >
                    Approve
                  </button>
                  <button
                    type="button"
                    onClick={() => onReject(request.request_id)}
                    className="rounded-md bg-state-failed px-3 py-1.5 text-xs font-medium text-white"
                  >
                    Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card>
        <h3 className="mb-3 text-sm font-medium text-text-secondary">Issued Tokens</h3>
        {tokens.length === 0 ? (
          <p className="text-sm text-text-muted">No tokens found.</p>
        ) : (
          <div className="space-y-2">
            {tokens.map((row) => (
              <div key={row.token_id} className="rounded-md border border-border bg-surface-0 p-3 text-xs">
                <div className="font-mono text-text-primary">{row.token_id}</div>
                <div className="text-text-secondary">
                  {row.subject} ({row.role}) - projects: {row.projects.join(", ")}
                </div>
                <div className="text-text-muted">active: {String(row.active)}</div>
                {row.active ? (
                  <button
                    type="button"
                    onClick={() => onRevoke(row.token_id)}
                    className="mt-2 rounded-md border border-state-failed px-2 py-1 text-[11px] text-state-failed"
                  >
                    Revoke
                  </button>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
