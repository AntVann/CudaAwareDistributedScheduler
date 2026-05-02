# Improvements Tracker

Findings from a live walkthrough of the dashboard against the SLURM control plane on `coe-hpc1`. Each item lists what was observed, why it matters, and a starting point for the fix. Work through them one at a time.

---

## Bugs

### 1. Nodes page is empty even though jobs get placed on `condo1` — FIXED

**Observed:** `/nodes` showed "No nodes registered yet." Dashboard tile "Node Freshness" showed `Total: 0`. But the Jobs page showed multiple jobs scheduled on `condo1`.

**Root cause(s):** Two independent bugs.
1. **Older SLURM rejects `GresUsed`.** `sinfo --Format=...,GresUsed` returns `Invalid job format specification: GresUsed`, so the text-parse fallback never produced rows. Fixed by retrying without `GresUsed` if the 5-column form fails (`control_plane/core/backends/slurm.py`, new `_run_sinfo_text` helper).
2. **`/api/nodes` was gated on `os.getenv("BACKEND") == "slurm"` re-read at request time.** When the env var wasn't visible to the request worker, the endpoint silently fell back to `persist_list_nodes()` which returned `[]`. Fixed by detecting the capability via `hasattr(backend, "list_nodes")` instead (`control_plane/api/nodes.py`).

**Verified:** Nodes page now shows `condo1` with `draining` state, partitions `compute,condo,gpu,lque`, and "2 GPUs available". `GET /api/nodes` returns the node with proper labels.

---

### 2. Misleading service badges on the Dashboard — FIXED

**Observed:** Dashboard showed "PostgreSQL Connected" and "Redis Connected" with green dots. Active config is `BACKEND=slurm`, `DATABASE_URL=sqlite://...`, `QUEUE_BACKEND=memory` — neither service was in use.

**Fix:** `/ready` already returns a `mode` field per subsystem (`"sqlite"`/`"memory"` in this deployment). Frontend now uses it to relabel the cards:

- Storage card: shows "SQLite / OK" when `ready.postgres.mode === "sqlite"`, otherwise "PostgreSQL / Connected".
- Queue card: shows "In-Memory Queue / OK" when `ready.redis.mode === "memory"`, otherwise "Redis / Connected".

Files: `frontend/src/api/client.ts` (added `mode` to `ReadyResponse`), `frontend/src/pages/Dashboard.tsx` (conditional labels).

**Verified:** Dashboard now reads "SQLite OK" and "In-Memory Queue OK" against the live HPC control plane.

---

### 3. "Based on Redis `jobs:queue` length" caption is wrong in SLURM mode — FIXED

**Observed:** Queue Depth tile literally referenced Redis even when the queue backend was `memory`.

**Fix:** Same `ready.redis.mode` switch as #2 — Queue Depth caption now reads "Pending jobs waiting for placement (in-memory queue)." in SLURM/SQLite mode and falls back to the original Redis caption when running against the real Redis-agent backend.

Files: `frontend/src/pages/Dashboard.tsx`.

**Verified:** Live dashboard shows the correct caption.

---

### 4. Failed placement leaves jobs in `QUEUED` with no reason — FIXED

**Observed:** `test-001` showed `QUEUED`, no SLURM ID, no node, `Reason: -`. There was no way to tell from the UI why placement didn't happen.

**Fix:** Reused the `placement_decision` blob shape introduced for #14. When `tick()` finds no eligible nodes, it now builds a "no placement" decision (`chosen_node_id: null`, every candidate marked rejected with its specific reason — `state=drained`, `partition mismatch (need X, have Y)`, `not enough GPUs (N available, M requested)`, etc.) and persists it via a new `set_placement_decision(job_id, decision)` helper that updates only the `placement_decision` column without touching state/timestamps.

The existing Placement decision panel in the Jobs row expansion now renders the no-placement case with a "STUCK · no eligible nodes" badge next to the panel title; the candidate table shows every node and the precise reason it was rejected. Each tick refreshes the blob, so operators always see the latest blocker.

Files: `control_plane/core/scheduler.py` (new `_build_no_placement_decision` + integration in `tick()`), `control_plane/core/persistence.py` (new `set_placement_decision`), `frontend/src/api/client.ts` (`chosen_node_id` is now nullable), `frontend/src/pages/Jobs.tsx` (STUCK badge), `tests/unit/test_scheduler.py` (new `test_tick_persists_no_placement_decision_when_no_eligible_nodes`).

---

### 5. Failed jobs show "FAILED" but no log pointer — FIXED (with #8)

**Observed:** `test-002` failed with exit code `127` (command not found). The Reason column just said `FAILED`. The actual stderr/stdout were in `SLURM_LOG_DIR` on the cluster but the UI gave no path.

**Fix:** SLURM submit already writes deterministic paths (`{log_dir}/{job_id}-{slurm_id}.{out|err}`), so no schema migration was needed. The backend learned to compute and read them on demand. See #8 for the full implementation — these two issues collapsed into one feature.

---

### 6. Latency tiles read 0 despite completed jobs — FIXED

**Observed:** Dashboard showed `Run P50/P95: 0` even with `test-003` / `live-test-1` having completed. Placement P50/P95 worked, so it was clearly a data-availability issue rather than a calc bug.

**Root cause:** The run-latency calc requires both a `running` and a terminal (`done`/`failed`/`cancelled`) timestamp on the job row. The SLURM poller's `_build_timestamps` *did* derive a `running` timestamp from sacct's `Start` field — but `set_job_state(...)` ignored it. It only ever wrote a single `state.lower()` keyed timestamp using `time.time()`. So when a job transitioned PLACED → RUNNING → DONE faster than the 10s poll interval (or was hydrated from SQLite at boot), the `running` key never made it into the `jobs.timestamps` JSON, and the calc skipped that row.

**Fix:**

- `set_job_state(job_id, state, ..., timestamps=None)` now accepts an optional caller-supplied timestamps dict. The Postgres path uses `extras::jsonb || existing || state_keyed::jsonb` so extras only fill in missing keys, existing values always win on conflict, and the new state's key always lands last with whichever value the caller provided (or `time.time()` if none). The SQLite path mirrors that semantically. Net effect: `running` derived from sacct survives, even when the poller leaps over the RUNNING state in a single tick.
- SLURM poller (`_poller_loop`) now forwards `status.timestamps` through to `set_job_state`.
- New unit tests cover both branches: a fast PLACED→DONE jump preserves `running`, and a separate explicit RUNNING transition is not later overwritten by a stale extras value.

Files: `control_plane/core/persistence.py`, `control_plane/core/backends/slurm.py`, `tests/unit/test_persistence.py` (fake-cursor signature), `tests/unit/test_persistence_sqlite.py` (two new test cases).

**Note for live verification:** historical jobs that completed *before* this fix will still have no `running` timestamp on disk and will continue to be excluded from run-latency. The tile will populate as soon as new jobs run end-to-end.

---

## UX gaps

### 7. No cancel button — FIXED

**Observed:** `CANCELLED` was in the documented lifecycle and the backend supported `scancel`, but the Jobs UI had no way to cancel.

**Fix:**

- API: `POST /api/jobs/{id}/cancel` (project-scoped via `require_user_or_admin`). Returns the updated `JobStatus`. Returns `409` if the job is already in a terminal state and `404` if the caller doesn't own the job's project.
- Backend interaction: for PLACED/RUNNING jobs the endpoint calls `backend.cancel(job_id)` (which runs `scancel`); for QUEUED jobs the SLURM call is skipped (no `backend_ref` exists yet). In all cases the job is marked CANCELLED in our DB with a reason that flags whether the backend accepted the cancel.
- Scheduler guard: `NaiveScheduler.tick()` now reads `get_job_status(job_id)` after popping from the queue and drops the job silently if it's no longer in QUEUED state. Without this, a job cancelled while waiting in line would still be dispatched to SLURM on the next tick. Covered by a new unit test (`test_tick_skips_jobs_already_cancelled_before_dispatch`).
- Frontend: `cancelJob(jobId, token)` in `client.ts`, plus a Cancel button at the top of the Jobs row expansion that only appears for `state ∈ {QUEUED, PLACED, RUNNING}` and is disabled when no token is set. Confirms via `window.confirm` before firing.

Files: `control_plane/api/jobs.py`, `control_plane/core/scheduler.py`, `frontend/src/api/client.ts`, `frontend/src/pages/Jobs.tsx`, `tests/unit/test_scheduler.py`.

---

### 8. No logs view in the UI — FIXED (paired with #5)

**Observed:** Even for completed jobs there was no way to see stdout/stderr in the dashboard.

**Fix:**

- Backend: added `SlurmBackend.log_paths(job_id)` and `SlurmBackend.read_logs(job_id, stream, tail)` to the SLURM backend. Paths are derived from `{SLURM_LOG_DIR}/{job_id}-{slurm_id}.{out|err}` — the same template `_generate_batch_script` writes into the `#SBATCH --output/--error` directives. `read_logs` reads the file with utf-8/replace error handling, returns the last `tail` lines, and reports `exists`, `bytes_total`, `lines`, and `truncated` so the UI can show "file not yet written" vs. "showing tail of N lines".
- API: added `GET /api/jobs/{job_id}/logs?stream=stdout|stderr&tail=N`. `stream` is validated via FastAPI `Query(pattern=...)`, `tail` is clamped 1..5000. Returns `404` if the job has no `backend_ref` yet (never submitted to SLURM), `501` if the active backend doesn't expose `read_logs` (e.g. redis-agent backend), and `200` with the payload otherwise. Crucially the endpoint returns `200 + exists:false` for the "submitted but no output yet" case so the UI can render the path even before the file is on disk.
- Frontend: `frontend/src/api/client.ts` exports `JobLogsResponse` and `fetchJobLogs(jobId, stream, tail)`. `frontend/src/pages/Jobs.tsx` adds a `JobLogsPanel` component embedded in the row expansion: stderr / stdout toggle, a refresh button, the absolute path + lines/bytes/truncation hint, and a scrollable `<pre>` block with the tail. When the job has no SLURM ID yet, it renders a friendly placeholder instead of a 404.

Files: `control_plane/core/backends/slurm.py`, `control_plane/api/jobs.py`, `frontend/src/api/client.ts`, `frontend/src/pages/Jobs.tsx`.

**Verified:** Live `GET /api/jobs/trace-001/logs?stream=stderr&tail=50` returns the correct path (`/tmp/scheduler-logs/trace-001-112362.err`), `exists=true`, full 11-line content, and 523-byte total. Dashboard row expansion renders the same payload as a tailed code block under a "Logs" panel, with stderr/stdout toggle and refresh wired to the endpoint.

Stretch goal still open: SSE-based live tailing instead of pull-on-click + manual refresh. Not worth doing until we're tailing genuinely long-running jobs.

---

### 9. Policy buttons appear active in read-only mode — FIXED

**Observed:** Sidebar said "Read-only mode" with no token, but the FIFO/ROUND_ROBIN/BINPACK buttons on the Dashboard looked fully clickable; clicking them silently failed.

**Fix:**

- Added `!token` to the policy buttons' `disabled` predicate so they go visually grayed-out the moment auth drops.
- Added `title=` tooltips that explain the disabled reason ("Read-only mode: enter an admin token in the sidebar to switch policies" vs. "Already the active policy").
- Replaced the muted "Read-only mode" label with a more visible warning-color line: "Read-only — admin token required to change policy". When a token *is* set the label stays as before ("API token set" in muted text).

In practice, the post-auth-merge code already gates `fetchPolicies` on a token, so in pure read-only mode the buttons don't render at all. This change covers the edge case of a stale `policies` object after a token is cleared mid-session, and makes the read-only state much more legible.

Files: `frontend/src/pages/Dashboard.tsx`.

---

### 10. Jobs table has no filtering, search, or sort

**Observed:** Plain table, ordered by some implicit default. No state filter, no job-id search.

**Why it matters:** Once the SQLite DB has hundreds of rows from past runs, the table is unusable.

**Where to look:** `frontend/src/pages/Jobs.tsx`. Add: state multi-select, job-id substring filter, time-range filter, click-to-sort headers. Either client-side (cheap, fine up to ~1k rows) or push down to `/api/jobs?state=...&limit=...` query params.

---

### 11. Auto-refresh is opt-in and off by default

**Observed:** Checkbox at top of Jobs page, unchecked initially. Dashboard probably also static.

**Why it matters:** This is a live ops dashboard. The default should be live.

**Where to look:** `Jobs.tsx` and `Dashboard.tsx` — flip default state, polling at 3–5s. Optionally: switch to SSE / websockets so we don't poll.

---

## Operational / scope

### 12. No retention or "last N hours" filter

**Observed:** Dashboard counters and Jobs table accumulate forever from SQLite. Old demo runs pollute the live view.

**Why it matters:** Can't tell what just happened vs. what happened a week ago. Eventually impacts performance too.

**Where to look:** Add a time-window param to `/api/jobs` and `/api/metrics/summary`. Optionally a soft archive flag on old rows.

---

### 13. Submit form exposes only `cmd` / `gpus` / `partition`

**Observed:** The `JobSpec` model defines `cpu`, `mem_gb`, `priority`, `env`. None of those are in the Submit Test Job form.

**Why it matters:** Forces users to drop to curl for any non-trivial job. We support more than we expose.

**Where to look:** `frontend/src/pages/Jobs.tsx` submit panel. Add the missing fields. Probably also rename the button — "Submit Test Job" undersells it.

---

### 14. No "why this node?" decision trace per job — FIXED

**Observed:** Job rows showed `Node: condo1` but no record of *why* — FIFO first match, BINPACK best fit, or RR rotation?

**Fix:**

- Schema: added `placement_decision TEXT` (SQLite) / `JSONB` (Postgres) column to `jobs`. SQLite migration runs idempotently in `bootstrap_storage()` via `PRAGMA table_info(jobs)` + `ALTER TABLE ... ADD COLUMN`.
- Scheduler: `NaiveScheduler._select_node()` now returns `(node_id, decision_dict)`. The decision blob captures: `policy`, `partition`, `requested_gpus`, `chosen_node_id`, `chosen_reason` (per-policy human-readable why), `candidates` (every recently-seen node with `available_gpu / gpu_count / avg_utilization / partitions / state / eligible / selected` plus `rejected_reason` when filtered out and `score` for BINPACK), `decided_at`, optional `round_robin_pointer` for RR.
- Persistence: `place_job(job_id, node_id, decision=...)` writes the blob alongside the PLACED transition. Backfilled into `list_jobs()` row dicts.
- UI: Jobs row expansion now renders a "Placement decision" panel — top summary (policy / partition / requested GPUs / why / decided-at), then a candidates table where the chosen node is highlighted, eligible alternatives are shown in normal text, and rejected nodes show the rejection reason in muted text.

Files: `control_plane/core/persistence.py`, `control_plane/core/scheduler.py`, `control_plane/db/schema.sql`, `frontend/src/api/client.ts`, `frontend/src/pages/Jobs.tsx`.

**Verified:** `_select_node` invoked with three synthetic candidates (condo1 idle gpu, gpu7 mix gpu, gpu9 idle compute-only) produces the expected blob — condo1 selected by FIFO, gpu9 rejected with "partition mismatch (need gpu, have compute)". Same blob round-trips through SQLite and renders correctly in the live Jobs page row expansion.

Stretch goal still open: a "policy comparison" page that replays a queue under FIFO/RR/BINPACK and shows makespan/utilization deltas.

---

## Suggested order

1. **#1 (Nodes empty)** — foundation; everything else assumes it works.
2. **#14 (Decision trace)** — the killer differentiator.
3. **#5 + #8 (Logs)** — the most-asked operator feature.
4. **#7 (Cancel)** — completes the lifecycle.
5. **#4, #6 (Reasons + latency)** — credibility fixes.
6. **#2, #3, #9, #11 (UI honesty + defaults)** — quick wins.
7. **#10, #12, #13 (filtering, retention, full submit form)** — usability polish.
