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
3. `curl http://localhost:8000/api/nodes` — expect both `node-a` and `node-b` with GPU inventories, labels, `agent_health`, and recent `last_seen` timestamps.
4. Optionally stop one agent (`docker compose stop agent-a`) and watch subsequent responses show only the remaining node.

## Milestone 4 Testing

Milestone 4 introduces the scheduling loop and agent worker so jobs progress from `QUEUED → PLACED → RUNNING → DONE/FAILED`.

1. `make up` to launch Redis, Postgres, the control plane, and both agents.
2. Enqueue a job (e.g. `curl -X POST http://localhost:8000/api/jobs -H 'Content-Type: application/json' --data '{"job_id":"m4-demo","image":"alpine","cmd":["echo","hello"],"gpus":1}'`).
3. Watch its lifecycle: `watch -n 2 curl http://localhost:8000/api/jobs/m4-demo` — expect `PLACED` within a second, `RUNNING`, then `DONE`.
4. View scheduler/worker logs via `make logs` (control plane places jobs, agents claim assignments and execute).
5. Agents now poll `/api/nodes/{node}/assignments/next`. By default they simulate execution; to run on a host with real GPUs set `EXECUTOR_MODE=force` (or `auto` with `nvidia-smi` available) and `GPU_CAPACITY` to match your device before starting the agent container/process. Submit a GPU workload (e.g. `cmd:["nvidia-smi"]`) and observe the real command running on your RTX-equipped machine.
6. Jobs always finish even on CPU-only laptops thanks to the simulator; production deploys (Milestones 5+) will replace the mock metrics/executor with NVML/Apptainer integrations.
