# CudaAwareDistributedScheduler

# CUDA Overlay - Milestone 1 (Scaffold & Local Compose)

**Goal**: Stand up the repo, containers, and minimal FastAPI services.

## Services & Ports
- Control Plane: http://localhost:8000/health
- Agent A:      http://localhost:8001/health
- Agent B:      http://localhost:8002/health
- Postgres:     localhost:5432
- Redis:        localhost:6379

## Quickstart
```bash
make up
make logs
```

## Milestone 2 Testing

The `milestone-2` branch introduces the job enqueue/status APIs, bootstrap logic, and Redis/Postgres wiring. To validate:

1. `make up` - builds the images and starts Redis, Postgres, the control plane, and both agents.
2. `make logs` - watch until Postgres is healthy and the control plane reports "Bootstrapping storage OK".
3. `curl http://localhost:8000/ready` - should return HTTP 200 with both stores marked `ok: true`.
4. Enqueue a job:
   ```
   curl -X POST http://localhost:8000/api/jobs ^
     -H "Content-Type: application/json" ^
     --data "{\"job_id\":\"m2-smoke\",\"image\":\"alpine\",\"cmd\":[\"echo\",\"hi\"]}"
   ```
5. Fetch its status:
   ```
   curl http://localhost:8000/api/jobs/m2-smoke
   ```
   Expect `state: "QUEUED"` and `timestamps.enqueued` populated.
6. Optional deep checks:
   - `docker compose -f deploy/docker-compose.yml exec redis redis-cli lrange jobs:queue 0 -1`
   - `docker compose -f deploy/docker-compose.yml exec postgres psql -U overlay -d overlay -c "select job_id,status from jobs;"`
   - `curl http://localhost:8000/api/policies` (shows active policy and supported list).

Bring everything down with `make down` when finished.

## Milestone 3 Testing

Milestone 3 adds live agent registration and fake GPU heartbeats.

1. `make up` (if not already running).
2. Wait for the agents to start; they send a heartbeat every ~5 seconds.
3. `curl http://localhost:8000/api/nodes` - expect both `node-a` and `node-b` with GPU inventories, labels, `agent_health`, and recent `last_seen` timestamps.
4. Optionally stop one agent (`docker compose stop agent-a`) and watch subsequent responses show only the remaining node.

## Milestone 4 Testing

Milestone 4 adds a naive scheduler loop, admin state transitions, and a fake worker that dequeues jobs.

1. Start the stack: `make up`.
2. Enqueue a job (same as Milestone 2): `curl -X POST http://localhost:8000/api/jobs -H "Content-Type: application/json" --data "{\"job_id\":\"m4-smoke\",\"image\":\"alpine\",\"cmd\":[\"echo\",\"hi\"]}"`.
3. Watch status transitions to `PLACED`/`RUNNING`/`DONE`:
   - `watch -n1 curl -s http://localhost:8000/api/jobs/m4-smoke` (or rerun the curl manually).
4. Inspect Redis queues (optional):
   - Global queue should shrink after placement: `docker compose -f deploy/docker-compose.yml exec redis redis-cli llen jobs:queue`
   - Per-node assign queue: `docker compose -f deploy/docker-compose.yml exec redis redis-cli lrange assign:node-a 0 -1`
5. Confirm admin endpoint works directly (optional): `curl -X POST http://localhost:8000/api/admin/jobs/m4-smoke/state -H "Content-Type: application/json" --data "{\"state\":\"RUNNING\"}"`.

## Milestone 4.1 (Local GPU support)

Milestone 4.1 keeps the scheduler/worker but adds real GPU metrics via NVML for hosts with NVIDIA GPUs (e.g., RTX 3070). Jobs still run via the fake executor.

1. Ensure NVIDIA drivers and CUDA are installed on the host; `nvidia-smi` should work.
2. Start the stack with GPU visibility: `make up`. The agent containers request GPUs (`NVIDIA_VISIBLE_DEVICES=all`); if Docker needs an explicit flag, run `docker compose --compatibility -f deploy/docker-compose.yml up --build -d --gpus all`.
3. Metrics mode is controlled by `GPU_METRICS_MODE` (default `auto`): set to `real` to require NVML, or `fake` to force synthetic metrics.
4. Enqueue a job as in Milestone 4 and watch status; heartbeats should now report real GPU names/utilization/temperature from NVML.
5. Optional: run the agent directly on the host for debugging:
   ```
   python -m venv .venv && .\.venv\Scripts\activate
   pip install -r requirements.txt
   set CONTROL_PLANE_API=http://localhost:8000
   set GPU_METRICS_MODE=real
   uvicorn agent.agent:app --host 0.0.0.0 --port 8001
   ```

## Milestone 5 (Real execution + NVML)

Milestone 5 keeps NVML metrics and switches the worker to run the job command for real (host or Apptainer if `image` is set).

1. Ensure NVIDIA drivers/toolkit are installed; `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` should work.
2. Start the stack with GPU access: `docker compose --compatibility -f deploy/docker-compose.yml up --build -d`. The agent will try NVML; set `GPU_METRICS_MODE=real` to require it.
3. Enqueue a job:
   ```
   curl -X POST http://localhost:8000/api/jobs -H "Content-Type: application/json" --data "{\"job_id\":\"m5-smoke\",\"image\":\"\",\"cmd\":[\"nvidia-smi\"]}"
   ```
   - If you provide `image`, the worker runs `apptainer exec --nv <image> <cmd>` (ensure Apptainer is installed in the agent container/host).
   - Without `image`, the command runs on the host.
4. Poll status: `curl http://localhost:8000/api/jobs/m5-smoke` until you see `DONE` or `FAILED`.
5. To observe GPU memory/utilization changes, enqueue a heavier command (e.g., a Python script allocating GPU tensors) either on host or inside an Apptainer image.
