# Milestone 8.1: Auth/Authz Branch Runbook

Status: Implemented and HPC-validated  
Last updated: 2026-04-28  
Branch: `feature/authz-user-project-v1`

## Goal

Add practical token-based authentication and project-scoped authorization to the scheduler without introducing full login, OAuth, or SSO.

This branch now supports both:

1. local Docker development with Postgres + Redis
2. HPC SLURM deployment with SQLite + in-process queue

## What Is Implemented

1. Human roles backed by DB tokens: `admin`, `user`
2. Internal service role backed by env token: `agent`
3. Public token request endpoint: `POST /api/token-requests`
4. Admin request review endpoints for approve/reject/revoke flows
5. Project-scoped job submit/read authorization
6. Identity endpoint: `GET /api/me`
7. Protected reads for jobs, nodes, metrics, and policies
8. SQLite support for auth persistence on HPC
9. Two token delivery modes for approval:
   - `email`
   - `response`

## Current Auth Model

### Modes

1. `AUTH_MODE=token`
   - bearer auth is enforced
2. `AUTH_MODE=none`
   - auth is bypassed
   - useful only for trusted local or debugging scenarios

### Principal Types

1. `admin`
   - full read/write access
   - can approve or reject token requests
   - can revoke issued tokens
2. `user`
   - can read protected APIs
   - can submit and read jobs only for allowed projects
3. `agent`
   - can post node heartbeats
   - can update job state callbacks from workers or SLURM jobs

### Bootstrap Admin Token

On startup, when `AUTH_MODE=token` is enabled and no active admin token exists:

1. the control plane reads `ADMIN_API_TOKEN`
2. if not set, it falls back to `OPERATOR_API_TOKEN`
3. it stores only the token hash in the database
4. it creates a bootstrap admin principal with subject `bootstrap-admin`

This now works in both:

1. Postgres-backed local development
2. SQLite-backed SLURM/HPC mode

## Data Model

### `api_tokens`

Stores:

1. token hash
2. subject
3. role
4. project scopes
5. active flag
6. expiry
7. created metadata

In Postgres, `projects` is stored as JSONB.  
In SQLite, `projects` is stored as JSON text.

### `token_requests`

Stores:

1. requester name
2. requester email
3. requested project scopes
4. purpose
5. review status
6. review notes
7. reviewer metadata

In Postgres, `requested_projects` is stored as JSONB.  
In SQLite, `requested_projects` is stored as JSON text.

### `jobs`

Relevant auth fields:

1. `project`
2. `submitted_by`

These are used for project-scoped access checks.

## Endpoint Protection Matrix

### Public

1. `GET /health`
2. `GET /ready`
3. `GET /version`
4. `POST /api/token-requests`

### User or Admin

1. `GET /api/me`
2. `GET /api/jobs`
3. `GET /api/jobs/{job_id}`
4. `GET /api/jobs/summary`
5. `POST /api/jobs`
6. `GET /api/nodes`
7. `GET /api/metrics/summary`
8. `GET /api/policies`

### Admin Only

1. `PUT /api/policies/active`
2. `GET /api/admin/token-requests`
3. `POST /api/admin/token-requests/{id}/approve`
4. `POST /api/admin/token-requests/{id}/reject`
5. `GET /api/admin/tokens`
6. `POST /api/admin/tokens/{id}/revoke`

### Agent Only

1. `POST /api/nodes`
2. `POST /api/admin/jobs/{job_id}/state`

## Token Approval Delivery Modes

### `TOKEN_DELIVERY_MODE=email`

This is the default behavior.

Approval flow:

1. generate raw token
2. store only its hash
3. send raw token by SMTP email
4. commit approval only if email delivery succeeds

If SMTP fails, the request remains `PENDING`.

Use this mode when:

1. SMTP is configured
2. the runtime environment can reach the SMTP server

### `TOKEN_DELIVERY_MODE=response`

This is the HPC-friendly fallback.

Approval flow:

1. generate raw token
2. store only its hash
3. skip SMTP
4. return the raw token once in the admin approval API response

Example approval response shape:

```json
{
  "request_id": "57586884-e0b0-4eac-a0db-1fd3ea99715c",
  "status": "APPROVED",
  "token_id": "b3218545-0bc7-46d3-abec-31f343c5fcf6",
  "expires_at": "2026-07-27T05:32:57.151649",
  "plaintext_token": "ERi5fidWPGZ5TVINDzVoiC7mi-84S3d6IDlIuA0V-XI"
}
```

Use this mode when:

1. SMTP is unavailable
2. outbound email is blocked by the HPC network
3. admin-mediated token handoff is acceptable for the demo or deployment

## HPC Validation Summary

This branch was validated on the real SJSU SLURM environment with:

1. `BACKEND=slurm`
2. `DATABASE_URL=sqlite:///...`
3. `QUEUE_BACKEND=memory`
4. `AUTH_MODE=token`
5. `CONTROL_PLANE_CALLBACK_URL=http://g17.hpc.coe:8010`

The following were confirmed on HPC:

1. SLURM node discovery returns real GPU nodes
2. bootstrap admin token works in SQLite mode
3. `/api/me` works with admin and user tokens
4. authenticated job submission works
5. user-scoped SLURM job submission works
6. SLURM lifecycle advances through `QUEUED -> PLACED -> RUNNING -> DONE`
7. public token request creation works
8. admin approval works in `TOKEN_DELIVERY_MODE=response`

### HPC Email Limitation

SMTP email delivery was not fully usable from the tested HPC environment because outbound connections to Gmail SMTP could not be established from the cluster node.

This is why `TOKEN_DELIVERY_MODE=response` was added.  
It preserves the approval workflow without depending on SMTP.

## Local Development Usage

For local Docker development, use:

1. `AUTH_MODE=token`
2. `ADMIN_API_TOKEN=local-operator-token`
3. `AGENT_API_TOKEN=local-agent-token`
4. optional SMTP env vars if you want real email delivery

Typical behavior:

1. leave `TOKEN_DELIVERY_MODE` unset to use `email`
2. set `TOKEN_DELIVERY_MODE=response` if you want to bypass SMTP and retrieve tokens from the admin approval response

## HPC Startup Example

```bash
export BACKEND=slurm
export DATABASE_URL=sqlite:///~/scheduler-auth.db
export QUEUE_BACKEND=memory
export SLURM_DEFAULT_PARTITION=gpuqs
export CONTROL_PLANE_CALLBACK_URL=http://g17.hpc.coe:8010
export AUTH_MODE=token
export ADMIN_API_TOKEN=local-operator-token
export AGENT_API_TOKEN=local-agent-token
export TOKEN_DELIVERY_MODE=response

python -m uvicorn control_plane.app:app --host 0.0.0.0 --port 8010
```

## HPC Test Runbook

### 1. Start the control plane

Use the startup block above.

### 2. Verify admin auth

```bash
curl http://127.0.0.1:8010/health
curl -H "Authorization: Bearer local-operator-token" http://127.0.0.1:8010/api/me
curl -H "Authorization: Bearer local-operator-token" http://127.0.0.1:8010/api/policies
```

### 3. Submit a token request

```bash
EMAIL="user@example.com"

curl -X POST http://127.0.0.1:8010/api/token-requests \
  -H "Content-Type: application/json" \
  -d "{
    \"subject_name\": \"hpc-manual-user\",
    \"email\": \"$EMAIL\",
    \"requested_projects\": [\"default\"],
    \"purpose\": \"HPC auth workflow test\"
  }"
```

### 4. Approve and capture the returned token

```bash
REQ_ID="paste-request-id-here"

curl -X POST \
  "http://127.0.0.1:8010/api/admin/token-requests/$REQ_ID/approve" \
  -H "Authorization: Bearer local-operator-token" \
  -H "Content-Type: application/json" \
  -d '{"review_notes":"approved during HPC validation"}'
```

Copy `plaintext_token` from the response.

### 5. Verify the user token

```bash
USER_TOKEN="paste-token-here"

curl -H "Authorization: Bearer $USER_TOKEN" \
  http://127.0.0.1:8010/api/me
```

### 6. Submit a user-scoped SLURM job

```bash
curl -X POST http://127.0.0.1:8010/api/jobs \
  -H "Authorization: Bearer $USER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": "slurm-auth-user-response-flow-1",
    "project": "default",
    "image": "",
    "cmd": ["sh", "-c", "sleep 5; hostname"],
    "gpus": 1,
    "metadata": {"partition": "gpuqs"}
  }'
```

### 7. Check lifecycle status

```bash
curl -H "Authorization: Bearer $USER_TOKEN" \
  http://127.0.0.1:8010/api/jobs/slurm-auth-user-response-flow-1
```

Expected final state:

1. `project` is `default`
2. `node_id` is populated
3. `exit_code` is `0`
4. state reaches `DONE`

## Testing Summary

### Automated Tests

Current local verification includes:

1. SQLite auth persistence tests
2. auth unit tests
3. scheduler unit tests
4. SLURM backend unit tests
5. API contract tests for metrics and nodes

### Covered Behaviors

1. bootstrap admin token creation in SQLite
2. human token resolution in SQLite
3. expired token rejection in SQLite
4. token request create/list/approve/reject in SQLite
5. rollback on SMTP delivery failure
6. response-mode approval returning plaintext token
7. SQLite job project lookup and authenticated job reads

## Known Limitations

1. No OAuth, SSO, or username/password login
2. No canonical `projects` table
3. Token request rate limiting is still in-memory
4. `TOKEN_DELIVERY_MODE=response` exposes plaintext token once in the admin approval response, so it should only be used in trusted workflows
5. Some HPC environments may block outbound SMTP, making `TOKEN_DELIVERY_MODE=email` impractical there

## Security Notes

1. Raw tokens should be treated like passwords
2. Only token hashes are stored in the database
3. When using `TOKEN_DELIVERY_MODE=response`, the admin should copy the token securely and the user should rotate or revoke it if it is exposed
4. Any exposed SMTP credentials or issued raw tokens should be revoked immediately
