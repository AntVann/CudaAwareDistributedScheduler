import { useState } from "react";

import Card from "../components/Card";
import { submitTokenRequest } from "../api/client";

export default function RequestToken() {
  const [subjectName, setSubjectName] = useState("");
  const [email, setEmail] = useState("");
  const [projects, setProjects] = useState("default");
  const [purpose, setPurpose] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ ok: boolean; msg: string } | null>(null);

  async function onSubmit() {
    setSubmitting(true);
    setResult(null);
    try {
      const parsedProjects = projects
        .split(",")
        .map((project) => project.trim())
        .filter(Boolean);
      const response = await submitTokenRequest({
        subject_name: subjectName.trim(),
        email: email.trim(),
        requested_projects: parsedProjects,
        purpose: purpose.trim(),
      });
      setResult({
        ok: true,
        msg: `Request submitted (${response.request_id}). Admin approval is required.`,
      });
      setPurpose("");
    } catch (err) {
      setResult({
        ok: false,
        msg: err instanceof Error ? err.message : "Failed to submit request",
      });
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-3xl space-y-5">
      <h2 className="text-lg font-semibold">Request API Token</h2>
      <Card>
        <p className="mb-4 text-sm text-text-secondary">
          Submit a token request to the admin. Approved tokens are delivered by email.
        </p>
        <div className="space-y-3">
          <div>
            <label className="mb-1 block text-xs text-text-muted">Subject Name</label>
            <input
              value={subjectName}
              onChange={(event) => setSubjectName(event.target.value)}
              className="w-full rounded-md border border-border bg-surface-0 px-3 py-2 text-sm"
              placeholder="Jane Doe"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-text-muted">Email</label>
            <input
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              className="w-full rounded-md border border-border bg-surface-0 px-3 py-2 text-sm"
              placeholder="jane@example.com"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-text-muted">Projects (comma-separated)</label>
            <input
              value={projects}
              onChange={(event) => setProjects(event.target.value)}
              className="w-full rounded-md border border-border bg-surface-0 px-3 py-2 text-sm"
              placeholder="default, vision"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs text-text-muted">Purpose</label>
            <textarea
              value={purpose}
              onChange={(event) => setPurpose(event.target.value)}
              className="w-full rounded-md border border-border bg-surface-0 px-3 py-2 text-sm"
              rows={4}
              placeholder="Need access for milestone validation."
            />
          </div>
          <button
            type="button"
            onClick={onSubmit}
            disabled={submitting}
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-hover disabled:opacity-50"
          >
            {submitting ? "Submitting..." : "Submit Token Request"}
          </button>
          {result ? (
            <p className={`text-xs font-mono ${result.ok ? "text-state-done" : "text-state-failed"}`}>
              {result.msg}
            </p>
          ) : null}
        </div>
      </Card>
    </div>
  );
}
