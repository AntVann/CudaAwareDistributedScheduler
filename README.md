# CudaAwareDistributedScheduler

A GPU-aware job scheduler that runs on top of SLURM. It provides a REST API and web dashboard for submitting, tracking, and managing GPU workloads on HPC clusters.

**Key features:**
- FastAPI control plane with SLURM backend (sbatch/sacct/sinfo/scancel)
- SQLite persistence (no Postgres/Redis required on HPC)
- Runtime-selectable scheduling policies: `FIFO`, `ROUND_ROBIN`, `BINPACK`
- Background poller that tracks SLURM job state transitions
- React/Vite admin dashboard with real-time job and node views
- Also runs locally via Docker Compose with Redis + Postgres for development

## Architecture

```
  Browser (React UI)
       |
  FastAPI Control Plane   <-- REST API
       |
  ExecutionBackend (ABC)
       |
  +----+----+
  |         |
SLURM    Redis+Agent
(HPC)    (Local Dev)
```

The `ExecutionBackend` abstraction allows the same API to work against SLURM on HPC or Redis+Agent workers in Docker for local development.

## Project Structure

```
control_plane/          # FastAPI backend
  core/
    backend.py          # ExecutionBackend ABC
    backends/
      slurm.py          # SLURM backend (sbatch, sacct, sinfo, scancel)
      redis_agent.py    # Redis+Agent backend for local dev
    scheduler.py        # Job placement logic (FIFO, ROUND_ROBIN, BINPACK)
    persistence.py      # Dual-path: Postgres or SQLite
    models.py           # JobSpec, JobStatus, NodeInfo, etc.
  api/                  # REST endpoints (jobs, nodes, metrics, policies)
  db/schema.sql         # Postgres schema
frontend/               # React/Vite admin dashboard
agent/                  # Worker agent (local dev mode only)
deploy/                 # Docker Compose files
docs/                   # Design documents and reports
tests/                  # Unit and integration tests
```

## Quick Start: HPC Deployment (SLURM)

This is the primary deployment mode, running on an HPC cluster with SLURM.

### Prerequisites

- Access to a SLURM HPC cluster (tested on SJSU coe-hpc1/coe-hpc3)
- Python 3.10+ available via `module load`
- SLURM commands available: `sbatch`, `sacct`, `sinfo`, `scancel`, `squeue`

### 1. Transfer the project to HPC

From your local machine:
```bash
rsync -avz \
  --exclude='.venv' --exclude='node_modules' --exclude='.git' \
  --exclude='__pycache__' --exclude='frontend/dist' --exclude='*.pyc' \
  ~/Projects/CudaAwareDistributedScheduler <your-id>@<hpc-login-node>:~/
```

### 2. Set up Python environment on HPC

```bash
# SSH into the HPC login node
module load python3/3.11.5    # or whichever 3.10+ is available
cd ~/CudaAwareDistributedScheduler
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install fastapi uvicorn pydantic
```

### 3. Start the control plane

```bash
export BACKEND=slurm
export DATABASE_URL="sqlite:////home/<your-id>/scheduler.db"
export OPERATOR_TOKEN="demo-token-123"
export SLURM_POLL_INTERVAL_SECS=10

cd ~/CudaAwareDistributedScheduler
python -m uvicorn control_plane.app:app --host 0.0.0.0 --port 8000
```

You should see:
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### 4. Submit a test job

From a second terminal on the cluster:
```bash
curl -X POST http://<login-node>:8000/api/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "job_id": "test-001",
    "image": "",
    "cmd": ["nvidia-smi"],
    "gpus": 1,
    "metadata": {"partition": "gpuqs"}
  }'
```

Check job status:
```bash
curl http://<login-node>:8000/api/jobs | python3 -m json.tool
```

Check SLURM queue:
```bash
squeue -u <your-id>
```

### 5. View the dashboard from your local machine

Set up an SSH tunnel from your local machine:
```bash
ssh -L 8000:<login-node>:8000 <your-id>@<hpc-login-node>
```

Then start the frontend locally:
```bash
cd frontend
npm install
echo 'VITE_API_BASE=http://localhost:8000' > .env
npm run dev
```

Open `http://localhost:5173` in your browser to see the dashboard.

### Environment Variables (SLURM mode)

| Variable | Default | Description |
|----------|---------|-------------|
| `BACKEND` | `redis-agent` | Set to `slurm` for HPC mode |
| `DATABASE_URL` | — | `sqlite:///path/to/scheduler.db` |
| `SLURM_POLL_INTERVAL_SECS` | `15` | How often to poll sacct for job updates |
| `SLURM_DEFAULT_PARTITION` | `gpu` | Default SLURM partition if not specified in job |
| `SLURM_LOG_DIR` | `/tmp/scheduler-logs` | Where SLURM stdout/stderr logs go |
| `SLURM_SCRIPT_DIR` | `/tmp/scheduler-scripts` | Where generated sbatch scripts are stored |
| `CONTROL_PLANE_CALLBACK_URL` | `http://127.0.0.1:8000` | URL compute nodes use to call back to the control plane |
| `AGENT_API_TOKEN` | — | Token for agent-scope auth on callbacks |
| `OPERATOR_TOKEN` | — | Token for operator-scope auth |

## Quick Start: Local Development (Docker)

For local development and testing without an HPC cluster.

### Prerequisites

- Docker Engine/Desktop running
- Docker Compose v2 (`docker compose`)
- `make`
- Node.js (for frontend)

### 1. Start the stack

```bash
make up
```

This starts the control plane, two agent workers, Redis, and Postgres.

### 2. Verify health

```bash
curl http://localhost:8000/health
curl http://localhost:8000/api/nodes
```

### 3. Start the frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173` in your browser.

### 4. Submit a job

From the UI, click **Submit Test Job**, or via API:
```bash
curl -X POST http://localhost:8000/api/jobs \
  -H "Authorization: Bearer local-operator-token" \
  -H "Content-Type: application/json" \
  -d '{"job_id":"demo-1","image":"","cmd":["echo","hello"]}'
```

Watch the job move through `QUEUED -> PLACED -> RUNNING -> DONE` in the Jobs page.

### 5. Change scheduler policy

On the Dashboard page, switch between `FIFO`, `ROUND_ROBIN`, and `BINPACK`.

### 6. Stop

```bash
make down
```

### Services and Ports (Docker mode)

| Service | Port |
|---------|------|
| Control plane | `http://localhost:8000` |
| Agent A | `http://localhost:8001` |
| Agent B | `http://localhost:8002` |
| Redis | `localhost:6379` |
| Postgres | internal only |

## API Reference

### Jobs

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/jobs` | Submit a new job |
| `GET` | `/api/jobs` | List all jobs |
| `GET` | `/api/jobs/{job_id}` | Get job status |
| `GET` | `/api/jobs/summary` | Job counts by state |

**JobSpec fields:**
```json
{
  "job_id": "my-job",
  "image": "",
  "cmd": ["nvidia-smi"],
  "gpus": 1,
  "cpu": null,
  "mem_gb": null,
  "priority": 0,
  "env": {},
  "metadata": {"partition": "gpuqs"}
}
```

- `image`: container image path. Leave empty (`""`) to run the command directly on the host.
- `cmd`: command and arguments as a list.
- `gpus`: number of GPUs to request.
- `metadata.partition`: SLURM partition to submit to.

### Nodes

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/nodes` | List cluster nodes with GPU info |

### Metrics & Policies

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/metrics/summary` | Queue depth, latency, node counts |
| `GET` | `/api/policies` | Active policy and supported list |
| `PUT` | `/api/policies/active` | Change scheduling policy |

## Job Lifecycle

```
QUEUED -> PLACED -> RUNNING -> DONE
                            -> FAILED
                            -> CANCELLED
```

- **QUEUED**: Job accepted, waiting for scheduler to place it
- **PLACED**: Scheduler selected a node, sbatch submitted to SLURM
- **RUNNING**: SLURM started the job (or callback received)
- **DONE**: Job completed successfully (exit code 0)
- **FAILED**: Job failed (non-zero exit code, timeout, node failure)
- **CANCELLED**: Job was cancelled via scancel

## Testing

```bash
python3 -m venv .venv
.venv/bin/pip install -r dev-requirements.txt

# Unit tests
.venv/bin/python -m pytest tests/unit

# Lint
.venv/bin/python -m ruff check control_plane agent tests
```

## Troubleshooting

- **`sinfo: unrecognized option '--json'`**: Older SLURM version. The backend automatically falls back to text-based `sinfo` parsing.
- **Job fails with exit code 127**: The command (or `apptainer`) was not found on the compute node. Use `"image": ""` to run commands directly.
- **`sqlite3.OperationalError: near "ON": syntax error`**: SQLite too old for `ON CONFLICT` upserts. The code uses `INSERT OR REPLACE` which works on all versions.
- **`sacct` returns "accounting storage is disabled"**: `sacct` must be run from a node with access to the SLURM accounting database (typically the login node, not compute nodes).
- **Jobs stay QUEUED**: Check that `sinfo` returns nodes. If no nodes are found, the scheduler can't place jobs.
- **Docker: `bind: address already in use`**: Run `make down` then `make up`.
