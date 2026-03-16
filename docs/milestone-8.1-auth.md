# Milestone 8.1: Auth/Authz + Token Request Workflow

Status: Implemented  
Date: 2026-03-15  
Branch: `feature/authz-user-project-v1`

## Goal

Move from static env-only operator auth to a simple user-level, project-scoped auth model suitable for demos and team usage, without adding full login/OAuth.

## What Is Implemented

1. Human roles backed by DB tokens: `admin`, `user`.
2. Internal service role: `agent` via `AGENT_API_TOKEN`.
3. Public token request endpoint: `POST /api/token-requests`.
4. Admin approval/rejection workflow for token requests.
5. Email delivery of newly approved raw token through SMTP.
6. Project-scoped authorization for job submit/read.
7. Identity endpoint: `GET /api/me`.
8. Protected read APIs (`jobs`, `nodes`, `metrics`, `policies`).

## Auth Model

### Modes

1. `AUTH_MODE=token` (default in local compose): auth enabled.
2. `AUTH_MODE=none`: auth bypass for local fallback.

### Principal Types

1. `admin`
   - Full read/write access across all projects.
   - Can approve/reject token requests and revoke tokens.
2. `user`
   - Can read protected data and submit/read jobs only within assigned projects.
3. `agent`
   - Can post node heartbeats and update job state.
   - Not requestable from UI.

### Bootstrap

On startup, if `AUTH_MODE=token` and there is no active admin token in DB:
1. Control plane reads `ADMIN_API_TOKEN` (fallback `OPERATOR_API_TOKEN`).
2. Inserts a hashed bootstrap admin token (`subject=bootstrap-admin`, role `admin`).

## Data Model (Current)

### `api_tokens`

1. `id` UUID PK
2. `token_hash` SHA-256 hash (raw token is never stored)
3. `subject`
4. `role` (`admin|user|agent` supported by schema; human auth currently accepts `admin|user`)
5. `projects` JSONB array
6. `active` bool
7. `expires_at`
8. `created_at`, `created_by`

### `token_requests`

1. `id` UUID PK
2. `subject_name`
3. `email`
4. `requested_projects` JSONB array
5. `purpose`
6. `status` (`PENDING|APPROVED|REJECTED`)
7. `review_notes`
8. `reviewed_by`
9. `created_at`, `reviewed_at`

### `jobs` extensions

1. `project` (required, default/backfill `default`)
2. `submitted_by` (nullable)

## Endpoint Protection Matrix

### Public

1. `GET /health`
2. `GET /ready`
3. `GET /version`
4. `POST /api/token-requests` (rate-limited per IP in-memory)

### User or Admin

1. `GET /api/me`
2. `GET /api/jobs`
3. `GET /api/jobs/{job_id}`
4. `GET /api/jobs/summary`
5. `POST /api/jobs` (plus project-scope check for non-admin)
6. `GET /api/nodes`
7. `GET /api/metrics/summary`
8. `GET /api/policies`

### Admin only

1. `PUT /api/policies/active`
2. `GET /api/admin/token-requests`
3. `POST /api/admin/token-requests/{id}/approve`
4. `POST /api/admin/token-requests/{id}/reject`
5. `GET /api/admin/tokens`
6. `POST /api/admin/tokens/{id}/revoke`

### Agent only

1. `POST /api/nodes`
2. `POST /api/admin/jobs/{job_id}/state`

## Token Request and Approval Flow

1. User submits `POST /api/token-requests` with subject, email, projects, purpose.
2. Request is stored as `PENDING`.
3. Admin approves via `/api/admin/token-requests/{id}/approve`.
4. System generates random raw token, stores only `token_hash`, and attempts SMTP send.
5. Approval transaction commits only after email send succeeds.
6. If SMTP fails, transaction rolls back and request remains `PENDING`.

## SMTP Configuration (Real Email)

Configured through env:
1. `SMTP_HOST`
2. `SMTP_PORT`
3. `SMTP_USER`
4. `SMTP_PASS`
5. `SMTP_FROM`
6. `SMTP_STARTTLS`

Local usage pattern:
1. Copy `.env.example` to `.env`.
2. Set Gmail SMTP values and app password.
3. Start stack via `make up`.

Note:
1. `Makefile` compose commands explicitly use `--env-file .env` so SMTP secrets are injected reliably.
2. Raw token email body includes a `TOKEN: <value>` line.

## Frontend Auth UX (Current)

1. Sidebar token input stores token in `sessionStorage`.
2. UI probes `/api/me` to resolve identity and role.
3. `/request-token` page allows public token request submission.
4. `/admin/token-requests` page is admin-only for approve/reject/revoke.
5. Jobs submit now requires `project`.
6. For `user` role, Jobs page uses allowed projects from `/api/me` as a dropdown (no manual project typing).

## Tests (Current)

1. Unit tests cover auth checks, persistence, metrics, scheduler, worker behavior.
2. Integration tests cover:
   - unauthenticated protected endpoint rejection
   - agent/admin scope behavior
   - token request creation and approval
   - issued token metadata checks via admin APIs
   - project-scoped job access semantics
3. Integration suite no longer depends on Mailpit inbox scraping.

## Known Gaps and Deferred Items

1. No username/password login or OAuth/SSO.
2. No canonical `projects` table yet (project values are scoped string labels on tokens/jobs).
3. Token request IP rate limiter is in-memory (not distributed).
4. Revocation `reason` payload is accepted at API edge but not persisted as first-class token metadata.
5. Command stdout/stderr persistence/return is still out of scope for this milestone.

## Manual Verification Checklist

1. `GET /health`, `/ready`, `/version` without token should pass.
2. Protected reads without token should return `401`.
3. Submit token request and approve as admin.
4. Confirm raw token arrives by SMTP email.
5. `GET /api/me` with user token should show scoped projects.
6. User submit to allowed project should pass.
7. User submit to other project should return `403`.
8. User policy update should return `403`.
9. Admin policy update should pass.
10. Admin calling agent-only endpoint should return `403`.
