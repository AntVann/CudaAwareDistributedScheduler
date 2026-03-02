# CudaAwareDistributedScheduler

Milestone 8 prototype for a CUDA-aware overlay scheduler with:
- FastAPI control plane
- Agent heartbeats + worker loop
- Redis queue + Postgres state
- Runtime-selectable `FIFO`, `ROUND_ROBIN`, and inventory-based `BINPACK` scheduler policies
- Host command execution and optional Apptainer execution path
- Token auth for control-plane mutations
- React/Vite admin UI with metrics and policy controls
- Unit tests, lifecycle integration test, and CI quality gates

## Prerequisites

Required:
- Docker Engine/Desktop running
- Docker Compose v2 (`docker compose`)
- `make`
- `curl`

Optional:
- Python 3.11+ (for local CLI usage)
- NVIDIA driver + container toolkit (if you want real GPU metrics in containers)

## Services and Ports

- Control plane: `http://localhost:8000`
- Agent A: `http://localhost:8001`
- Agent B: `http://localhost:8002`
- Redis: `localhost:6379`
- Postgres: internal to Docker network (`postgres:5432`, not published on host)

## Demo Walkthrough

This is the recommended end-to-end local demo flow for the current Milestone 8 system.

What this demo shows:
- control plane readiness
- authenticated operator actions
- agent heartbeats
- job submission and lifecycle tracking
- metrics summary updates
- runtime scheduler policy changes

What this demo does not show yet:
- SLURM or Milestone 9 HPC integration

### 1. Start the stack

```bash
make up
```

Optional:
```bash
make logs
```

### 2. Use the local demo tokens

The local Compose setup already configures these values:
- operator token: `local-operator-token`
- agent token: `local-agent-token`

For the demo, the main one you will use manually is:
```bash
local-operator-token
```

### 3. Verify the platform is healthy

```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/version
curl -s http://localhost:8000/ready
curl -s http://localhost:8000/api/nodes
```

Expected:
- `/health` returns `ok: true`
- `/ready` shows both Postgres and Redis as healthy
- `/api/nodes` shows active nodes heartbeating

### 4. Open the admin UI

Start the frontend in a separate terminal:

```bash
cd frontend
npm install
npm run dev
```

Then open:
```text
http://localhost:5173
```

In the sidebar:
- enter `local-operator-token` into the operator token field

### 5. Show the dashboard

UI path:
- `Admin UI -> Dashboard`

From the UI, show:
- health and readiness cards
- queue depth
- current job counts
- fresh vs stale node counts
- latency summary
- active scheduling policy

Or do an optional API check:
```bash
# Current operator-facing metrics snapshot: queue depth, node freshness,
# latency percentiles, and recent terminal job counts.
curl -s "http://localhost:8000/api/metrics/summary?window_minutes=60"

# Current active scheduler policy and the list of supported policies.
curl -s http://localhost:8000/api/policies
```

### 6. Submit a smoke-test job

UI path:
- `Admin UI -> Jobs -> Submit Test Job`

From the UI:
- go to `Jobs`
- click `Submit Test Job`
- keep the default command or use:
```json
["echo", "hello-from-demo"]
```

Or submit from the API:
```bash
# Submit one authenticated host-mode test job to the control plane.
# This should create a new job, enqueue it, and let the agents process it.
curl -s -X POST http://localhost:8000/api/jobs \
  -H "Authorization: Bearer local-operator-token" \
  -H "Content-Type: application/json" \
  --data '{"job_id":"demo-job-1","image":"","cmd":["echo","hello-from-demo"]}'
```

### 7. Track the job lifecycle

UI path:
- `Admin UI -> Jobs`

In the UI:
- stay on the `Jobs` page
- enable auto-refresh
- watch the job move through `QUEUED -> PLACED -> RUNNING -> DONE`

Or do an optional API check:
```bash
curl -s http://localhost:8000/api/jobs/demo-job-1
curl -s http://localhost:8000/api/jobs
curl -s http://localhost:8000/api/jobs/summary
```

### 8. Change scheduler policy live

UI path:
- `Admin UI -> Dashboard -> Scheduling Policy`

On the dashboard:
- switch between `FIFO`, `ROUND_ROBIN`, and `BINPACK`
- show that the active policy changes immediately

Or do an optional API check:
```bash
curl -s -X PUT http://localhost:8000/api/policies/active \
  -H "Authorization: Bearer local-operator-token" \
  -H "Content-Type: application/json" \
  --data '{"policy":"BINPACK"}'

curl -s http://localhost:8000/api/policies
```

### 9. Show metrics after running jobs

UI path:
- `Admin UI -> Dashboard`

After one or more jobs complete:
- return to the dashboard
- show queue depth returning to zero
- show current job counts and terminal counts updating
- show placement and run latency summaries populated

Or do an optional API check:
```bash
curl -s "http://localhost:8000/api/metrics/summary?window_minutes=60"
```

### 10. Stop the demo

```bash
make down
```

## Run Modes

### Local Dev Mode (Mac + non-GPU machines)

Uses the base compose file and `GPU_METRICS_MODE=auto` (falls back to fake metrics when NVML is unavailable).

Compose now enables bearer-token auth by default:
- `AUTH_MODE=token`
- operator token: `local-operator-token`
- agent token: `local-agent-token`

This is intentionally insecure for local development, but it exercises the Milestone 8 auth flow end to end.

1. Start the stack:
```bash
make up
```

2. Follow logs:
```bash
make logs
```

3. Verify control plane readiness:
```bash
curl -s http://localhost:8000/health
curl -s http://localhost:8000/version
curl -s http://localhost:8000/ready
```

4. Verify nodes are heartbeating:
```bash
curl -s http://localhost:8000/api/nodes
```

5. If using `curl` for mutating APIs, include the operator token:
```bash
curl -s -X POST http://localhost:8000/api/jobs \
  -H "Authorization: Bearer local-operator-token" \
  -H "Content-Type: application/json" \
  --data '{"job_id":"smoke-1","image":"","cmd":["echo","hello-from-worker"]}'
```

Compatibility mode:
- set `AUTH_MODE=none` on the control plane to disable auth checks entirely
- this keeps older local flows working while migrating, but Compose uses token mode by default

### GPU Mode (NVIDIA hosts only)

Use this only on Linux/WSL2 environments with NVIDIA GPU runtime configured for Docker.

```bash
make up-gpu
make logs-gpu
```

This mode applies `/deploy/docker-compose.gpu.yml` and forces:
- `GPU_METRICS_MODE=real`
- NVIDIA GPU device reservation for both agent services

On Apple Silicon macOS (M1/M2/M3), this mode is not supported for NVML/CUDA containers.

## Submit and Track a Job

1. Enqueue:
```bash
curl -s -X POST http://localhost:8000/api/jobs \
  -H "Authorization: Bearer local-operator-token" \
  -H "Content-Type: application/json" \
  --data '{"job_id":"smoke-1","image":"","cmd":["echo","hello-from-worker"]}'
```

2. Poll status:
```bash
curl -s http://localhost:8000/api/jobs/smoke-1
```

3. Optional CLI watcher:
```bash
make cli
.venv/bin/python cli/cli.py watch smoke-1
```

If your system `python3` is 3.13 and you see local package build issues, use Python 3.12 for the venv:
```bash
python3.12 -m venv .venv
.venv/bin/pip install -r cli/requirements.txt
.venv/bin/python cli/cli.py watch smoke-1
```

Expected lifecycle is `QUEUED -> PLACED -> RUNNING -> DONE` (or `FAILED`).

Duplicate `job_id` submissions are idempotent:
- first submission returns `201` with `"created": true`
- repeated submission returns `200` with `"created": false` and the existing job status

Protected mutating endpoints in `AUTH_MODE=token`:
- `POST /api/jobs` requires the operator token
- `POST /api/nodes` requires the agent token
- `POST /api/admin/jobs/{job_id}/state` requires the agent token
- `PUT /api/policies/active` requires the operator token

## Execution Modes

- Host mode: if `image` is empty, worker runs command directly.
- Image mode: if `image` is non-empty, worker runs:
```bash
apptainer exec --nv <image> <cmd...>
```

Important:
- The current `agent/Dockerfile` does not install Apptainer.
- Image mode therefore requires either:
  - a custom agent image with Apptainer installed, or
  - running the agent on a host that already has Apptainer.
- If image mode is requested without `apptainer` available, the job fails explicitly with exit code `127` and a clear reason.

## Testing and CI

Create a local dev environment:
```bash
python3.12 -m venv .venv
.venv/bin/pip install -r dev-requirements.txt
```

Run local quality gates:
```bash
.venv312/bin/python -m compileall -q control_plane agent cli tests
.venv312/bin/python -m ruff check control_plane agent cli tests
.venv312/bin/python -m pytest tests/unit
```

Run the lifecycle integration test against a live compose stack:
```bash
make up
RUN_INTEGRATION=1 .venv312/bin/python -m pytest tests/integration
```

GitHub Actions:
- PR and `main` pushes run compile, lint, and unit tests
- integration test is available through manual workflow dispatch

If your default local venv uses Python 3.13, prefer the documented Python 3.12 venv for backend tooling:
```bash
python3.12 -m venv .venv312
.venv312/bin/pip install -r dev-requirements.txt
```

## GPU Metrics

- `GPU_METRICS_MODE=auto` (default): try NVML, fall back to fake metrics
- `GPU_METRICS_MODE=real`: require NVML (raises if unavailable)
- `GPU_METRICS_MODE=fake`: always synthetic metrics

For real GPU metrics in containers, Docker runtime and host GPU setup must be correct.

## Metrics and Policy APIs

Operator-facing read APIs:
- `GET /api/jobs/summary`
- `GET /api/metrics/summary`
- `GET /api/policies`

Metrics summary:
```bash
curl -s "http://localhost:8000/api/metrics/summary?window_minutes=60"
```

Policy read/update:
```bash
curl -s http://localhost:8000/api/policies

curl -s -X PUT http://localhost:8000/api/policies/active \
  -H "Authorization: Bearer local-operator-token" \
  -H "Content-Type: application/json" \
  --data '{"policy":"BINPACK"}'
```

`BINPACK` caveat:
- it uses latest heartbeat inventory and average GPU utilization only
- it does not subtract GPUs already committed to `PLACED` or `RUNNING` jobs
- Redis security and reservation-aware accounting are intentionally deferred beyond milestone 8

## Useful Commands

Start:
```bash
make up
```

Start with NVIDIA GPU override:
```bash
make up-gpu
```

Stop and remove volumes:
```bash
make down
```

Stop GPU mode stack:
```bash
make down-gpu
```

Raw compose config check:
```bash
docker compose -f deploy/docker-compose.yml config -q
```

Raw compose config check (GPU mode):
```bash
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.gpu.yml config -q
```

## Known Gaps (Current Prototype)

- Redis still has no auth or network hardening in the prototype architecture
- `BINPACK` is inventory-based and heuristic, not reservation-aware
- No SLURM adapter yet (`deploy/slurm/env.sample` is placeholder only)

## Troubleshooting

- `docker compose` fails immediately:
  - ensure Docker daemon is running
- `bind: address already in use` on `5432`:
  - fixed in current compose by not publishing Postgres on host
  - if you still have old containers, run `make down` then `make up`
- `could not select device driver "" with capabilities: [[gpu]]`:
  - you started GPU mode on a machine/runtime without NVIDIA Docker support
  - use `make up` for local dev mode, or configure NVIDIA runtime and use `make up-gpu`
- Jobs stay `QUEUED`:
  - check agent heartbeats with `GET /api/nodes`
- Jobs fail with Apptainer errors:
  - run host mode (`"image":""`) or install Apptainer in agent runtime
