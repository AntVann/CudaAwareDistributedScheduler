# CudaAwareDistributedScheduler

Milestone 5 prototype for a CUDA-aware overlay scheduler with:
- FastAPI control plane
- Agent heartbeats + worker loop
- Redis queue + Postgres state
- Naive round-robin scheduler
- Host command execution and optional Apptainer execution path

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

## Run Modes

### Local Dev Mode (Mac + non-GPU machines)

Uses the base compose file and `GPU_METRICS_MODE=auto` (falls back to fake metrics when NVML is unavailable).

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

## GPU Metrics

- `GPU_METRICS_MODE=auto` (default): try NVML, fall back to fake metrics
- `GPU_METRICS_MODE=real`: require NVML (raises if unavailable)
- `GPU_METRICS_MODE=fake`: always synthetic metrics

For real GPU metrics in containers, Docker runtime and host GPU setup must be correct.

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

- No auth/authz between services
- No full CI/lint/test gates yet
- Scheduler policy selection API exists, but scheduler behavior is currently naive round-robin
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
