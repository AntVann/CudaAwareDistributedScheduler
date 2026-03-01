# Project Status - 2026-02-15

## Current Branch Snapshot
- Branch: `codex/milestone-6` (based on current `main`)
- Scope: Milestone 6 reliability + quality gates
- Local state: milestone 6 implementation in progress locally with tests and CI additions
- Baseline checks: compile, lint, unit tests, and lifecycle integration test pass locally

## What Is Working Now
1. Control-plane API is running with `/health`, `/version`, and `/ready`.
2. Job intake is implemented:
   - `POST /api/jobs` stores job spec and queues job in Redis
   - `GET /api/jobs/{job_id}` returns current state from Postgres
3. Scheduler loop is implemented:
   - Pops jobs from `jobs:queue`
   - Finds recently active nodes
   - Pushes assignment to `assign:<node_id>`
   - Marks job as `PLACED`
4. Agent heartbeat path is implemented:
   - Agents periodically post node info to `/api/nodes`
   - Control plane stores/upserts node inventory and `last_seen`
5. Worker execution path is implemented:
   - Worker blocks on `BLPOP assign:<node_id>`
   - Gets job spec from Redis
   - Updates state to `RUNNING`, then `DONE` or `FAILED`
6. Execution modes are implemented:
   - Host mode: executes command directly
   - Container/image mode: uses `apptainer exec --nv <image> <cmd>`
7. GPU metrics path is implemented:
   - NVML metrics in `auto`/`real` mode
   - Fake GPU metrics fallback in `fake` or auto-fallback cases
8. CLI helper exists:
   - `python cli/cli.py watch <job_id>` for polling lifecycle to completion
9. Milestone 6 quality gates are implemented:
   - Unit tests for scheduler, persistence, and worker paths
   - Integration lifecycle test for `QUEUED -> PLACED -> RUNNING -> DONE`
   - GitHub Actions workflow for compile, lint, and unit tests
10. Milestone 6 runtime hardening is implemented:
   - Worker state updates retry with bounded backoff
   - Duplicate `job_id` submission is deterministic and documented
   - Image-mode execution fails clearly when `apptainer` is unavailable

## What Is Partially Implemented
1. Scheduling policy support is declared (`FIFO`, `ROUND_ROBIN`, `BINPACK`) but scheduler logic is currently simple round-robin.
2. State model includes full lifecycle states, but transition rules are permissive and not strongly validated.
3. Events table exists in schema, but event emission/audit trail is not yet used in runtime paths.

## Milestone 6 Status

Milestone 6 is complete from a project-deliverable perspective.

Completed deliverables:
1. Automated tests:
   - Unit tests for scheduler, persistence/idempotency, and worker failure handling
   - Integration lifecycle test for `QUEUED -> PLACED -> RUNNING -> DONE`
2. CI quality gates:
   - GitHub Actions workflow added for compile, lint, and unit tests
   - Integration test workflow available through manual dispatch
3. Runtime hardening:
   - Retry/backoff for worker state updates
   - Deterministic duplicate submission behavior
   - Explicit `apptainer` missing-runtime failure path

Validation completed locally:
1. `python -m compileall -q control_plane agent cli tests`
2. `ruff check control_plane agent cli tests`
3. `pytest tests/unit`
4. `RUN_INTEGRATION=1 pytest tests/integration`

## Follow-Up Bugs and Gaps

These do not block calling milestone 6 complete, but they should be tracked as follow-up reliability issues:

1. Worker drop-on-exception gap:
   - In `agent/worker.py`, if `process_job()` throws unexpectedly after `BLPOP`, the job is only logged and then dropped from the worker loop.
   - Impact: jobs can remain stuck without a terminal state.
2. Duplicate submission recovery gap:
   - In `control_plane/core/persistence.py`, if Postgres insert succeeds but Redis queue/spec write fails, a retry returns the existing job and does not repair Redis state.
   - Impact: jobs can be orphaned in Postgres and never scheduled.
3. Make target environment gap:
   - `make lint` and `make test` currently call bare `python3`.
   - Impact: the documented commands are not reliable unless the required tooling is installed in the active interpreter.

## HPC Integration Status
Yes, this is still pending and is likely the next major milestone.

### Still Needed for School HPC
1. SLURM integration layer:
   - submit (`sbatch`)
   - status tracking (`squeue`/`sacct`)
   - cancellation (`scancel`)
2. State mapping:
   - map SLURM states to overlay states (`QUEUED`, `PLACED`, `RUNNING`, `DONE`, `FAILED`, `CANCELLED`)
3. Deployment model decisions:
   - where control plane runs
   - how agents run (login/service/per-node)
   - networking/auth model for cluster boundaries
4. Runtime packaging:
   - Apptainer availability and image strategy on cluster
   - GPU partition and account/QOS settings
5. Pilot validation:
   - run a small GPU job set on the school cluster
   - verify lifecycle timing, failures, and retries

## Next Milestones (Proposed)

### Milestone 7 - Frontend Baseline (Admin UI + Smoke Testing)
Focus: add a lightweight frontend so the current system can be operated, demonstrated, and smoke-tested without relying only on curl and logs.

Deliverables:
1. Admin dashboard shell:
   - frontend app scaffold integrated with the existing control plane
   - simple local run/build workflow
2. Jobs UI:
   - submit a smoke-test job
   - list recent jobs
   - view lifecycle state, node assignment, exit code, and failure reason
3. Nodes UI:
   - show active nodes, GPU inventory, and last heartbeat
4. Platform status UI:
   - show `/health` and `/ready` status
   - display current queue/job summary data available from backend APIs
5. Frontend baseline for later milestones:
   - layout/components that observability and policy controls can extend in later work

Exit criteria:
- A user can submit and observe a smoke-test job through the UI.
- A user can inspect node status and platform readiness from the UI.
- The frontend is usable as the baseline admin page for future observability and policy work.

### Milestone 8 - Security + Observability + Policy Execution
Focus: make the system safer and more operable for multi-user/multi-node environments, using the frontend baseline from milestone 7.

Deliverables:
1. Service auth/authz baseline:
   - token-based authentication for mutating endpoints (`/api/jobs`, `/api/nodes`, `/api/admin/...`)
   - corresponding admin UI authentication flow or operator token entry
2. Observability baseline:
   - structured metrics for queue depth, placement latency, run duration, success/failure counts
   - admin UI panels for those metrics
3. Scheduler policy implementation:
   - implement behavior for declared policies (`FIFO`, `ROUND_ROBIN`, `BINPACK`) instead of static RR
   - expose current policy state in the admin UI

Exit criteria:
- Unauthorized requests are rejected for mutating APIs.
- Core service metrics are emitted and visible in the admin UI.
- Policy setting changes scheduler behavior in tests and is inspectable from the UI.

### Milestone 9 - SLURM Vertical Slice (School Cluster)
Focus: integrate with HPC scheduler while preserving overlay state model.

Deliverables:
1. SLURM adapter:
   - submit (`sbatch`)
   - status/reconcile (`squeue`, `sacct`)
   - cancel (`scancel`)
2. State mapping and reconciliation:
   - Map SLURM job states to overlay states (`QUEUED`, `PLACED`, `RUNNING`, `DONE`, `FAILED`, `CANCELLED`)
   - Background reconciliation loop for delayed/out-of-band state changes
3. Cluster deployment baseline:
   - Control-plane placement decision
   - Agent runtime model (service/login/per-node)
   - Auth/network model for cluster boundaries

Exit criteria:
- One end-to-end SLURM-backed job flow completes with correct state progression.
- Cancellation and failure paths are verified on cluster.
- Pilot report captures observed timings/failure modes and required fixes.

### Milestone 10 (Optional) - Production Hardening
Focus: prepare for sustained multi-user workloads after initial SLURM success.

Deliverables:
1. Event/audit trail wiring (`events` table used in runtime paths)
2. Dead-letter/recovery handling for repeated failures
3. Capacity and soak tests with multi-agent concurrency

Exit criteria:
- Soak tests complete without stuck jobs or unbounded queue growth.
- Failure recovery path is exercised and documented.
