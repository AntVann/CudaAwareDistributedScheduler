# Project Status - 2026-02-15

## Current Branch Snapshot
- Branch: `serhat-milestone-5` (tracking `origin/milestone-5`)
- Scope: Milestone 5 prototype
- Local state: clean branch history, plus two untracked docs (`docs/`, `spec_d_diffs.md`)
- Baseline checks: Python compile passes, Docker Compose config parses

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

## What Is Partially Implemented
1. Scheduling policy support is declared (`FIFO`, `ROUND_ROBIN`, `BINPACK`) but scheduler logic is currently simple round-robin.
2. State model includes full lifecycle states, but transition rules are permissive and not strongly validated.
3. Events table exists in schema, but event emission/audit trail is not yet used in runtime paths.

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

### Milestone 6 - Reliability + Quality Gates
Focus: make current runtime behavior testable and predictable before HPC coupling.

Deliverables:
1. Add automated tests:
   - Unit tests for scheduler selection, state transitions, and worker error handling
   - Integration test for `QUEUED -> PLACED -> RUNNING -> DONE/FAILED`
2. Add CI quality gates:
   - Basic lint/format/check + test job in CI
3. Harden runtime paths:
   - Retry/backoff around worker state updates
   - Explicit idempotency behavior for duplicate job IDs
   - Clear failure when `image` mode is requested without Apptainer runtime

Exit criteria:
- CI required checks pass on every PR.
- Lifecycle integration test is green and reproducible.
- No silent state-update failures in worker logs.

### Milestone 7 - Security + Observability + Policy Execution
Focus: make the control plane safer and operable for multi-user/multi-node environments.

Deliverables:
1. Service auth/authz baseline:
   - Token-based authentication for mutating endpoints (`/api/jobs`, `/api/nodes`, `/api/admin/...`)
2. Observability baseline:
   - Structured metrics for queue depth, placement latency, run duration, success/failure counts
   - Basic dashboard/logging guidance for local and cluster environments
3. Scheduler policy implementation:
   - Implement behavior for declared policies (`FIFO`, `ROUND_ROBIN`, `BINPACK`) instead of static RR

Exit criteria:
- Unauthorized requests are rejected for mutating APIs.
- Core service metrics are emitted and visible.
- Policy setting changes scheduler behavior in tests.

### Milestone 8 - SLURM Vertical Slice (School Cluster)
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

### Milestone 9 (Optional) - Production Hardening
Focus: prepare for sustained multi-user workloads after initial SLURM success.

Deliverables:
1. Event/audit trail wiring (`events` table used in runtime paths)
2. Dead-letter/recovery handling for repeated failures
3. Capacity and soak tests with multi-agent concurrency

Exit criteria:
- Soak tests complete without stuck jobs or unbounded queue growth.
- Failure recovery path is exercised and documented.
