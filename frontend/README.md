# Frontend Admin UI

React/Vite admin UI for the CudaAwareDistributedScheduler control plane.

## Local Development

Install dependencies:
```bash
npm install
```

Run the Vite dev server:
```bash
npm run dev
```

By default the UI talks to `http://localhost:8000`. Override with:
```bash
VITE_API_BASE=http://localhost:8000 npm run dev
```

## Auth Flow

This milestone uses bearer tokens, not username/password login.

1. Open the UI
2. Enter API token in sidebar
3. Token is stored in `sessionStorage`
4. UI probes `/api/me` to determine identity (`admin` or `user`)

Local Compose defaults:
- bootstrap admin token: `local-operator-token`
- internal agent token: `local-agent-token`
- token emails are delivered through configured SMTP (`SMTP_*` env vars)

Users without token can submit a request from `/request-token`.

## Pages

Dashboard:
- polls `/health` and `/ready` publicly
- polls `/api/metrics/summary` and `/api/policies` with token
- policy changes require admin token

Jobs:
- reads `GET /api/jobs` with token
- submits `POST /api/jobs` with required `project` field
- for `user` role, project selection is sourced from `/api/me.projects` (dropdown)

Nodes:
- reads `GET /api/nodes` with token

Request Token:
- public form that submits `POST /api/token-requests`

Admin Token Requests:
- admin-only page for:
  - list pending token requests
  - approve/reject requests
  - list/revoke issued tokens

## Build and Lint

```bash
npm run lint
npm run build
```
