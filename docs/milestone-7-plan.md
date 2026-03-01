# Milestone 7 Plan: Frontend Baseline

Status: Proposed  
Date: 2026-03-01  
Branch baseline: `main` after milestone 6

## Goal

Add a lightweight admin frontend that becomes the primary operator surface for smoke testing, job tracking, node inspection, and future observability work.

## Why This Milestone Exists

The backend is now stable enough to operate through APIs, but the project still depends too heavily on `curl`, logs, and direct container inspection for demos and smoke tests.

A frontend-first milestone gives the project:
1. A better smoke-test workflow
2. A clearer demo surface
3. A foundation for later observability, auth, and scheduler-policy controls

## Scope

In scope:
1. Frontend app scaffold and local dev workflow
2. Admin dashboard page
3. Jobs page with smoke-test submission and lifecycle viewing
4. Nodes page with heartbeat/GPU inventory display
5. Backend API adjustments needed to support those views cleanly
6. Dev-time CORS support between frontend and FastAPI API

Out of scope:
1. Full auth/authz model
2. Full metrics stack
3. SLURM integration
4. Multi-tenant product UX
5. Production-grade frontend Docker deployment

## Desired UI Capabilities

### 1) Dashboard
Display:
1. Control-plane `/health`
2. Control-plane `/ready`
3. Basic job summary cards
4. Quick links to jobs and nodes

### 2) Jobs
Display:
1. Recent jobs
2. Job state, node, exit code, and reason
3. Timestamps when available

Actions:
1. Submit a smoke-test job from the UI
2. Refresh or auto-refresh job state

### 3) Nodes
Display:
1. Node IDs
2. Last heartbeat
3. GPU inventory and utilization data
4. Basic agent health details

## Proposed Deliverables

1. Frontend directory/app scaffold
2. Admin layout and navigation
3. Jobs page
4. Nodes page
5. Dashboard page
6. Required backend endpoints for list/summary views
7. CORS configuration for local development
8. Documentation for local frontend run/build flow

## Frontend Stack

Chosen stack:
1. React
2. Vite

Rationale:
1. Fast setup for a small admin UI
2. Good local development experience for smoke testing
3. Easy to extend later for charts, policy controls, and auth flows

## Backend/API Expectations

Prefer to reuse existing endpoints where possible:
1. `GET /health`
2. `GET /ready`
3. `GET /api/jobs/{job_id}`
4. `POST /api/jobs`
5. `GET /api/nodes`

Required additions for milestone 7:
1. `GET /api/jobs`
   - returns all jobs ordered by enqueue time descending
   - each item shaped as `{job_id, state, node_id, gpu_ids, timestamps, exit_code, reason}` (flat, consistent with `JobStatus` field names)
   - prototype scale is small enough that returning all jobs without pagination is acceptable
2. `GET /api/jobs/summary`
   - returns aggregate counts for dashboard cards
   - example shape: `{queued: N, placed: N, running: N, done: N, failed: N, cancelled: N}`

Notes:
1. Frontend-side counting from `GET /api/jobs` would work at prototype scale.
2. A dedicated summary endpoint is still the better baseline because it keeps dashboard logic simple and gives a clean contract for later observability work.

## CORS and Serving Model

### Local Development

Milestone 7 baseline:
1. Run the frontend as a separate Vite dev server locally
2. Run the FastAPI control plane separately
3. CORS is already enabled in FastAPI with `allow_origins=["*"]` — no additional work needed for local dev (optionally restrict to `http://localhost:5173` later)

Why this is the baseline:
1. It is the fastest way to build and iterate on the admin UI
2. It avoids mixing frontend build concerns into the control-plane service too early
3. It keeps Docker/frontend integration explicitly deferred instead of ambiguous

### Deferred Docker Integration

Not required for milestone 7, but valid future options are:
1. Separate frontend container serving the built SPA and proxying `/api`
2. Static asset mount inside the control-plane service
3. Reverse proxy setup with same-origin serving

Milestone 7 should state explicitly that Dockerized frontend serving is deferred.

## Execution Plan

1. Scaffold a React + Vite frontend
2. Review/document current FastAPI CORS behavior for Vite local development
3. Add backend list and summary endpoints needed by the UI
4. Build dashboard shell and shared layout
5. Build jobs page with smoke-test submission
6. Build nodes page
7. Document local dev workflow (`uvicorn` + `npm run dev`)
8. Smoke-test the full UI workflow locally

## Exit Definition

Milestone 7 is complete when:
1. A user can open an admin UI locally
2. A user can submit a smoke-test job from the UI
3. A user can observe job lifecycle status from the UI
4. A user can inspect node readiness and GPU inventory from the UI
5. The frontend uses React + Vite with a documented local dev flow
6. The backend exposes list/summary endpoints needed by the UI
7. The frontend is ready to host later observability and policy features
