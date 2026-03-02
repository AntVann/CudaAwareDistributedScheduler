# Milestone 8 Plan: Security, Observability, and Policy Execution

Status: Proposed  
Date: 2026-03-01  
Branch baseline: `main` after milestone 7

## Goal

Add the minimum security, observability, and scheduler-policy behavior needed to make the prototype safer to operate and easier to reason about before SLURM integration work begins.

This milestone should harden the current Milestone 7 admin workflow without turning the project into a full multi-tenant platform.

## Why This Milestone Exists

Milestone 7 made the system operable through a UI, but the current runtime still has three major gaps:
1. Any client can call mutating APIs
2. There is no explicit metrics contract for operator-facing health and performance visibility
3. Declared scheduling policies exist in the API surface, but only one placement behavior is actually implemented

Milestone 8 should close those gaps with a pragmatic baseline that still fits the current prototype architecture.

## Scope

In scope:
1. Token authentication for mutating control-plane endpoints
2. Operator token entry in the frontend
3. Metrics summary API for frontend observability panels
4. Scheduler policy persistence and runtime selection
5. Concrete implementations for `FIFO`, `ROUND_ROBIN`, and `BINPACK`
6. Local-dev and compose documentation for the new auth and metrics behavior
7. Unit and integration coverage for the new contracts

Out of scope:
1. Full user accounts or RBAC
2. OAuth, SSO, JWT issuance, or refresh-token flows
3. Per-agent unique identity or certificate-based trust
4. Full Prometheus/Grafana stack
5. Historical time-series storage beyond simple summary windows
6. Multi-tenant quotas or fairness guarantees
7. SLURM-aware scheduling
8. Redis-level authentication or network isolation hardening

## Current Baseline

As of Milestone 7:
1. `POST /api/jobs` is unauthenticated
2. `POST /api/nodes` is unauthenticated
3. `POST /api/admin/jobs/{job_id}/state` is unauthenticated
4. `GET /api/policies` exists, but policy selection is read-only and scheduler behavior is effectively round-robin
5. The frontend can operate the system, but it has no auth flow and no metrics beyond basic health and job counts

## Design Principles

Milestone 8 should follow these constraints:
1. Prefer one clear contract per feature over flexible but underspecified abstractions
2. Keep local development easy enough for a single student/operator to run through Docker Compose
3. Make auth scope-aware, even if token management remains simple
4. Expose metrics in a frontend-friendly JSON shape first
5. Be explicit about what is heuristic versus authoritative in scheduler decisions

## Auth Baseline

### Actors

Milestone 8 should distinguish between these callers:
1. Operator UI
   - submits jobs
   - changes scheduling policy
   - reads metrics and status
2. Agent service
   - posts node heartbeats
   - posts job lifecycle state changes
3. Anonymous read-only caller
   - may read public prototype status endpoints in local dev

### Token Model

Use static bearer tokens configured by environment variables.

Auth modes:
1. `AUTH_MODE=none`
   - default for backward compatibility
   - disables API auth checks entirely
2. `AUTH_MODE=token`
   - enables bearer-token auth for protected endpoints

Required environment variables when `AUTH_MODE=token`:
1. `OPERATOR_API_TOKEN=<secret>`
2. `AGENT_API_TOKEN=<secret>`

Rules:
1. Mutating operator endpoints require the operator token
2. Mutating agent endpoints require the agent token
3. Missing token returns `401`
4. Invalid token returns `401`
5. Valid token with wrong scope returns `403`

Prototype simplification:
1. Use one shared operator token for Milestone 8
2. Use one shared agent token for all agents for Milestone 8
3. Do not build token issuance, rotation UI, or persistent token storage in this milestone

### Auth Header Contract

Use:
```http
Authorization: Bearer <token>
```

### Endpoint Protection Matrix

Protected in Milestone 8:
1. `POST /api/jobs`
   - scope: `operator`
2. `POST /api/nodes`
   - scope: `agent`
3. `POST /api/admin/jobs/{job_id}/state`
   - scope: `agent`
4. `PUT /api/policies/active`
   - scope: `operator`

Read-only in Milestone 8:
1. `GET /health`
2. `GET /ready`
3. `GET /version`
4. `GET /api/jobs`
5. `GET /api/jobs/{job_id}`
6. `GET /api/jobs/summary`
7. `GET /api/nodes`
8. `GET /api/policies`
9. `GET /api/metrics/summary`

Notes:
1. Keeping read APIs open is acceptable for the prototype baseline
2. If needed later, Milestone 9 or 10 can tighten read access as well

### Frontend Auth UX

Milestone 8 should not add a full sign-in flow.

Frontend behavior:
1. Add an operator token input in the admin UI
2. Store the token in browser `sessionStorage`
3. Attach the token only to operator-scoped mutating requests
4. Show a clear inline error for `401` and `403`
5. Allow the UI to remain readable for unauthenticated users on read-only pages

### Local Dev / Compose Defaults

Document explicit dev tokens in Compose:
1. control plane receives `AUTH_MODE=token`
2. control plane receives a known `OPERATOR_API_TOKEN`
3. control plane receives a known `AGENT_API_TOKEN`
4. agents receive `AGENT_API_TOKEN`
5. frontend README documents the operator token for local smoke tests

This is intentionally insecure for local development but still exercises the auth path.

Compatibility note:
1. `AUTH_MODE=none` remains available so older tests and local flows can continue to run during migration
2. Milestone 8 should update Compose and integration tests to exercise `AUTH_MODE=token` by default

Deferred security note:
1. Milestone 8 protects control-plane HTTP mutations only
2. Agents still access Redis directly for assignments and job specs in the current architecture
3. Redis auth and network hardening are explicitly deferred beyond this milestone

## Observability Baseline

### Goal

Expose a small, stable metrics summary contract for the Milestone 7 admin UI.

Milestone 8 should not introduce a full metrics stack. It should expose backend-derived JSON summaries that the frontend can poll.

### Metrics API

Add:
1. `GET /api/metrics/summary`

Query parameters:
1. `window_minutes`
   - optional
   - default `60`

Response shape:
```json
{
  "queue_depth": 1,
  "jobs": {
    "queued": 1,
    "placed": 0,
    "running": 2,
    "done": 10,
    "failed": 1,
    "cancelled": 0
  },
  "nodes": {
    "total": 2,
    "fresh": 2,
    "stale": 0
  },
  "latency_ms": {
    "placement_p50": 420,
    "placement_p95": 870,
    "run_p50": 2100,
    "run_p95": 3400
  },
  "windowed_terminal_counts": {
    "done": 10,
    "failed": 1
  },
  "window_minutes": 60
}
```

### Metric Definitions

Definitions should be explicit:
1. `queue_depth`
   - Redis `LLEN jobs:queue`
2. `jobs.*`
   - current counts by state from the jobs table
3. `nodes.total`
   - total rows in `nodes`
4. `nodes.fresh`
   - nodes with `last_seen` within scheduler freshness window
5. `nodes.stale`
   - `total - fresh`
6. `placement_p50` and `placement_p95`
   - derived from `timestamps.placed - timestamps.enqueued`
   - computed only from jobs whose placement timestamp falls within the requested time window
   - only for jobs that have both timestamps
7. `run_p50` and `run_p95`
   - derived from terminal timestamp minus `timestamps.running`
   - computed only from jobs whose terminal timestamp falls within the requested time window
   - terminal timestamp is `done`, `failed`, or `cancelled`
8. `windowed_terminal_counts`
   - number of `DONE` and `FAILED` jobs within the requested time window

Semantics note:
1. `jobs.*` represents current point-in-time state counts
2. `windowed_terminal_counts` represents recent terminal outcomes within `window_minutes`
3. Latency percentiles are also windowed by `window_minutes`, not all-time

### Data Source Strategy

Use existing Postgres job rows and Redis queue length.

Computation strategy:
1. Metrics are computed inside the control plane, not in the frontend
2. Prototype baseline may calculate latency percentiles in Python after a bounded backend query filtered by `window_minutes`
3. A single SQL implementation is also acceptable, but not required for Milestone 8

Milestone 8 should not require:
1. a separate metrics database
2. a Prometheus server
3. event-stream ingestion

### Frontend Observability Panels

Add dashboard panels for:
1. Queue depth
2. Current jobs by state
3. Fresh versus stale nodes
4. Placement latency summary
5. Run duration summary
6. Recent success/failure counts

Polling:
1. Poll `GET /api/metrics/summary` every 5 seconds
2. Reuse existing read-path error handling patterns from Milestone 7

### Deferred Metrics Work

Not required for Milestone 8:
1. Prometheus exposition format
2. Alerting rules
3. Time-series charts
4. Per-node GPU time-series history

## Policy Execution

### Goal

Make scheduler policy a real runtime behavior instead of a declared placeholder.

### Policy API

Keep:
1. `GET /api/policies`

Add:
1. `PUT /api/policies/active`

Request shape:
```json
{
  "policy": "ROUND_ROBIN"
}
```

Response shape:
```json
{
  "active": "ROUND_ROBIN",
  "supported": ["FIFO", "ROUND_ROBIN", "BINPACK"]
}
```

### Policy Persistence

Persist the active policy in Postgres.

Add a small settings table, for example:
1. `scheduler_settings`
   - `singleton_key`
   - `active_policy`
   - `updated_at`
   - `updated_by`

Startup behavior:
1. if the table has a value, use it
2. otherwise seed from `SCHED_POLICY` env var if valid
3. otherwise default to `FIFO`

This avoids an environment-only setting that the UI cannot safely change at runtime.

Schema migration note:
1. Add `scheduler_settings` to `control_plane/db/schema.sql`
2. Keep the DDL idempotent with `CREATE TABLE IF NOT EXISTS`
3. Milestone 8 does not introduce a separate migration framework

Reload behavior:
1. Load the active policy once at control-plane startup
2. Cache the active policy in process memory for scheduler ticks
3. When `PUT /api/policies/active` succeeds, update both Postgres and the in-memory active policy immediately
4. Do not query Postgres for policy on every scheduler tick
5. Multi-replica policy propagation is out of scope for Milestone 8

### Policy Semantics

Queue order:
1. All policies remain FIFO with respect to global dequeue order
2. Policy only changes node selection among eligible nodes

Eligibility:
1. Only fresh nodes are eligible
2. A node is considered fresh using the scheduler's existing recent-node threshold
3. A node is considered GPU-eligible when `len(node.gpus) >= spec.gpus`

Policy definitions:

1. `FIFO`
   - select the first eligible node in sorted `node_id` order
   - deterministic, simple baseline

2. `ROUND_ROBIN`
   - select the next eligible node using a rotating index
   - current Milestone 7 behavior, but only across eligible nodes

3. `BINPACK`
   - select the eligible node with the smallest non-negative inventory-based GPU surplus:
     - `len(node.gpus) - spec.gpus`
   - tie-break by highest average reported GPU utilization
   - final tie-break by `node_id`

Important caveat:
1. `BINPACK` is heuristic only
2. The scheduler does not yet maintain authoritative GPU reservations
3. Placement decisions are therefore based on latest heartbeat inventory and utilization snapshots, not strict resource accounting
4. The `BINPACK` surplus formula in Milestone 8 does not subtract GPUs already committed to other `PLACED` or `RUNNING` jobs
5. Reservation-aware packing is deferred until the runtime has explicit GPU allocation accounting

Behavior when no node is eligible:
1. Do not place the job
2. Push it back onto `jobs:queue`
3. Leave state as `QUEUED`

### Frontend Policy UI

Add to the admin UI:
1. visible current active policy
2. visible supported policies
3. operator-only control to change active policy
4. inline success/error state for the mutation

## Backend Implementation Plan

### 1) Auth Middleware / Dependency

Add a shared auth helper for FastAPI endpoints:
1. parse bearer token
2. validate against configured token set
3. enforce required scope
4. raise `401` or `403` with consistent JSON error shape

### 2) Metrics Computation

Add persistence helpers to compute:
1. queue depth
2. node freshness counts
3. current job counts by state
4. latency percentiles from timestamp fields
5. terminal outcome counts inside a time window

### 3) Policy Storage and Scheduler Dispatch

Refactor scheduler tick logic so:
1. job dequeue remains shared
2. eligible-node selection is delegated by policy
3. active policy is loaded from persistent settings
4. behavior is unit-testable without running the whole background loop

### 4) Frontend Integration

Extend the Milestone 7 API client with:
1. operator auth header support
2. policy fetch and mutate calls
3. metrics summary fetch call

## Validation Plan

### Unit Tests

Required unit coverage:
1. auth helper returns `401` for missing token
2. auth helper returns `401` for invalid token
3. auth helper returns `403` for wrong-scope token
4. metrics summary handles empty database safely
5. metrics latency calculations ignore incomplete timestamp sets
6. `FIFO` selects first eligible node deterministically
7. `ROUND_ROBIN` cycles across eligible nodes
8. `BINPACK` chooses the smallest-fitting eligible node by the documented tie-break rules
9. policy persistence falls back correctly when DB setting is absent

### Integration Tests

Required integration coverage:
1. unauthenticated `POST /api/jobs` is rejected
2. unauthenticated `POST /api/nodes` is rejected
3. valid agent token allows heartbeat updates
4. valid operator token allows job submission
5. policy mutation changes the active policy returned by `GET /api/policies`
6. metrics summary returns expected keys and non-negative values

### Manual / Compose Smoke Tests

Required smoke checks:
1. start Compose stack with auth enabled
2. verify agents successfully heartbeat using agent token
3. open frontend and enter operator token
4. submit a smoke-test job from the UI
5. verify dashboard metrics update
6. switch policy from the UI and confirm the API reflects the new active policy

## Documentation Deliverables

Milestone 8 should update:
1. root `README.md`
2. `frontend/README.md`
3. Compose environment examples if added

Documentation should cover:
1. auth environment variables
2. local operator token usage
3. agent token wiring
4. metrics endpoint
5. policy endpoint and UI workflow

## Execution Plan

Recommended order:
1. Add auth helper and protect mutating endpoints
2. Update agents and frontend to send tokens
3. Add auth unit and integration tests alongside the auth changes
4. Document the local auth workflow
5. Add metrics summary backend endpoint
6. Add metrics tests alongside the metrics implementation
7. Add frontend observability panels
8. Add policy persistence and mutation endpoint
9. Refactor scheduler to dispatch by selected policy
10. Add policy behavior tests alongside the scheduler changes
11. Run compose smoke tests and update docs

## Exit Definition

Milestone 8 is complete when:
1. Mutating APIs reject missing or invalid tokens
2. Agents can still heartbeat and update job state with the documented agent token
3. The frontend can submit jobs and change policy with the documented operator token
4. The backend exposes a stable metrics summary API used by the frontend
5. The admin UI shows queue, node, and latency summary panels
6. `FIFO`, `ROUND_ROBIN`, and `BINPACK` are all implemented and covered by tests
7. Policy changes persist across control-plane restarts
8. README and frontend docs explain the new auth and observability workflow
