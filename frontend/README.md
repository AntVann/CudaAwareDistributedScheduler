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

Milestone 8 does not add a sign-in screen.

Instead:
1. Open the UI
2. Enter the operator token in the sidebar
3. The token is stored in `sessionStorage`
4. Only operator-scoped mutating requests attach the bearer token

Local Compose defaults:
- operator token: `local-operator-token`
- agent token: `local-agent-token`

Read-only pages remain usable without a token.

## Dashboard

The dashboard polls:
- `/health`
- `/ready`
- `/api/metrics/summary`
- `/api/policies`

It shows:
- queue depth
- current jobs by state
- fresh vs stale nodes
- placement and run latency percentiles
- recent `DONE` and `FAILED` terminal counts
- active scheduler policy with operator-only update controls

## Jobs Page

The jobs page:
- lists recent jobs from `GET /api/jobs`
- supports auto-refresh polling every 3 seconds
- submits test jobs through `POST /api/jobs`

Submitting a job requires the operator token.

## Nodes Page

The nodes page remains read-only and polls `GET /api/nodes` every 5 seconds.

## Build and Lint

```bash
npm run lint
npm run build
```

Note:
- the current local Node version may print a Vite warning if it is older than `20.19`
- the build still succeeds in the current project environment, but upgrading Node removes the warning
