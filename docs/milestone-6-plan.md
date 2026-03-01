# Milestone 6 Plan: Reliability + Quality Gates

Status: Completed  
Date: 2026-02-16  
Branch baseline: `main`

## Goal

Stabilize the current prototype by adding test coverage, CI quality gates, and runtime hardening so regressions are caught early and job lifecycle behavior is predictable.

## Scope

In scope:
1. Automated tests (unit + integration lifecycle test)
2. CI pipeline for lint/check/test
3. Worker/control-plane reliability hardening

Out of scope:
1. SLURM adapter and HPC integration
2. Full auth/authorization model
3. Full observability dashboards

## Success Criteria

1. A PR-triggered CI workflow runs and enforces required checks.
2. At least one end-to-end lifecycle test verifies `QUEUED -> PLACED -> RUNNING -> DONE/FAILED`.
3. Worker state updates are retried with bounded backoff and non-2xx responses are handled explicitly.
4. Duplicate job submission behavior is deterministic and documented.
5. Image execution fails clearly when `apptainer` is unavailable.

## Workstreams

### 1) Testing

Deliverables:
1. Unit tests for scheduler behavior (`control_plane/core/scheduler.py`)
2. Unit tests for job persistence/idempotency rules (`control_plane/core/persistence.py`)
3. Unit tests for worker state update flow and failure handling (`agent/worker.py`)
4. One integration test for full lifecycle using compose stack

Planned test files:
1. `tests/unit/test_scheduler.py`
2. `tests/unit/test_persistence.py`
3. `tests/unit/test_worker.py`
4. `tests/integration/test_lifecycle.py`

Test coverage priorities:
1. Queue pop/requeue when no nodes are available
2. Placement writes (`assign:<node_id>` + `PLACED`)
3. Worker transitions and terminal state write
4. Duplicate `job_id` behavior (no accidental duplicate queueing)
5. Image-mode failure path when `apptainer` binary is missing

### 2) CI Quality Gates

Deliverables:
1. Add workflow: `.github/workflows/ci.yml`
2. Add repo commands for checks:
   - `make test`
   - `make lint`
3. Keep `python -m compileall` as a fast syntax gate

Initial CI jobs:
1. Install dependencies (Python 3.12)
2. Run compile check
3. Run lint
4. Run unit tests
5. Optionally gate integration tests behind manual/label trigger if runtime is heavy

### 3) Runtime Hardening

Deliverables:
1. Retry/backoff for state update calls in `agent/worker.py`
2. Explicit response-code handling for control-plane updates
3. Idempotency rule in `enqueue_job` to prevent duplicate queueing side effects
4. Clear error message when `image` execution is requested without `apptainer` in path

Implementation targets:
1. `agent/worker.py`
2. `agent/executor.py`
3. `control_plane/core/persistence.py`
4. `control_plane/api/jobs.py` (if response semantics need improvement for duplicates)

## Execution Plan (Suggested Order)

1. Implement runtime hardening first (small, high-impact behavior fixes)
2. Add unit tests covering new behavior
3. Add integration lifecycle test
4. Add CI workflow and make targets
5. Update README with test/CI usage

## Validation Commands

Local checks:
```bash
python3 -m compileall -q control_plane agent cli
make test
```

Stack smoke:
```bash
make up
curl -s http://localhost:8000/ready
curl -s http://localhost:8000/api/nodes
```

Lifecycle smoke:
```bash
JOB_ID="smoke-$(date +%s)"
curl -s -X POST http://localhost:8000/api/jobs \
  -H "Content-Type: application/json" \
  --data "{\"job_id\":\"$JOB_ID\",\"image\":\"\",\"cmd\":[\"echo\",\"hello\"]}"
.venv/bin/python cli/cli.py watch "$JOB_ID"
```

## Risks and Mitigations

1. Risk: Integration tests become flaky due to startup timing.
   - Mitigation: Add readiness polling and bounded retries before assertions.
2. Risk: CI runtime gets too slow.
   - Mitigation: Keep unit tests mandatory; run heavier integration tests selectively.
3. Risk: Behavior changes around duplicate jobs break clients.
   - Mitigation: Document exact API semantics and assert with tests.

## Completion Notes

Completed in the repository:
1. Unit tests were added for scheduler, persistence/idempotency, and worker state-update paths.
2. Integration lifecycle coverage was added for `QUEUED -> PLACED -> RUNNING -> DONE`.
3. CI workflow was added for compile, lint, and unit-test checks, with integration coverage available through manual dispatch.
4. Worker state updates now retry with bounded backoff and log non-2xx responses explicitly.
5. Duplicate submission behavior is deterministic and documented.
6. Image execution failure is explicit when `apptainer` is unavailable.

Known follow-up gaps after milestone completion:
1. If `process_job()` throws unexpectedly after `BLPOP`, the assignment can still be dropped without a terminal state update.
2. If Postgres insert succeeds and Redis queue/spec write fails during enqueue, retries do not currently repair Redis state.
3. `make lint` and `make test` rely on bare `python3`, so they are only reliable when the active interpreter has the dev tooling installed.
