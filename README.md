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
