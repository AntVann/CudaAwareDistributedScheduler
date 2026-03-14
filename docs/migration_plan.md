# Migration Plan: SLURM Backend Integration

**Status:** Proposed
**Date:** March 10, 2026
**Baseline:** main after Milestone 8

---

## Overview

This plan describes how to refactor CudaAwareDistributedScheduler from a standalone scheduler (with custom agents and Redis-based dispatch) into a system that can run on top of SLURM while preserving all existing functionality for local development.

The central change is introducing an `ExecutionBackend` abstraction. The scheduler's placement logic, API, database, and frontend remain intact. Only the job dispatch and node discovery mechanisms become pluggable.

---

## Phase 1: Backend Abstraction Layer

**Goal:** Decouple the scheduler from direct Redis dispatch without changing any external behavior.

### 1.1 Define the ExecutionBackend interface

Create `control_plane/core/backend.py`:

```python
from abc import ABC, abstractmethod
from control_plane.core.models import JobSpec, JobStatus, NodeInfo

class ExecutionBackend(ABC):
    """
    Abstraction over how jobs are dispatched and monitored.
    The scheduler decides WHERE to place a job. The backend
    decides HOW to execute that decision.
    """

    @abstractmethod
    def submit(self, spec: JobSpec, node_hint: str | None = None) -> str:
        """
        Dispatch a job for execution.
        Returns a backend-specific job reference (e.g., Redis queue entry, SLURM job ID).
        """
        ...

    @abstractmethod
    def poll_status(self, job_id: str) -> JobStatus | None:
        """
        Check the current state of a submitted job.
        Returns None if the backend has no information about this job.
        """
        ...

    @abstractmethod
    def list_nodes(self, recent_secs: int = 30) -> list[NodeInfo]:
        """
        Return the currently available compute nodes with GPU inventory.
        Used by the scheduler to find eligible placement targets.
        """
        ...

    @abstractmethod
    def cancel(self, job_id: str) -> bool:
        """
        Attempt to cancel a running or queued job.
        Returns True if cancellation was accepted.
        """
        ...
```

### 1.2 Wrap existing code as RedisAgentBackend

Create `control_plane/core/backends/redis_agent.py`:

This backend wraps the current behavior with zero logic changes:

- `submit()` → `redis.rpush(f"assign:{node_hint}", job_id)` + cache spec in Redis
- `poll_status()` → reads from PostgreSQL (agents POST state updates via `/api/admin/jobs/{job_id}/state`, which writes to Postgres — this path is unchanged)
- `list_nodes()` → queries PostgreSQL `nodes` table for recent heartbeats (same as current `_recent_nodes()`)
- `cancel()` → not implemented yet, returns False

### 1.3 Refactor scheduler.py

Current `tick()` method directly calls Redis and persistence functions. Refactor to:

```python
class NaiveScheduler:
    def __init__(self, backend: ExecutionBackend, loop_secs=1, recent_secs=30):
        self.backend = backend
        self.loop_secs = loop_secs
        self.recent_secs = recent_secs
        self.active_policy = SchedulerPolicy.FIFO

    def tick(self):
        job_id = redis_client().lpop("jobs:queue")
        if not job_id:
            return

        spec = get_job_spec(job_id)
        nodes = self.backend.list_nodes()
        eligible = [n for n in nodes if len(n.gpus) >= spec.gpus]

        if not eligible:
            redis_client().lpush("jobs:queue", job_id)
            return

        target = self._select_node(eligible, spec)
        self.backend.submit(spec, node_hint=target.node_id)
        place_job(job_id, target.node_id)
```

The `_select_node()` method (FIFO, ROUND_ROBIN, BINPACK logic) is completely unchanged.

### 1.4 Update app.py startup

```python
backend_type = os.getenv("BACKEND", "redis-agent")

if backend_type == "slurm":
    from control_plane.core.backends.slurm import SlurmBackend
    backend = SlurmBackend()
elif backend_type == "redis-agent":
    from control_plane.core.backends.redis_agent import RedisAgentBackend
    backend = RedisAgentBackend()
else:
    raise ValueError(f"Unknown backend: {backend_type}")

scheduler = NaiveScheduler(backend=backend, loop_secs=1)
```

### 1.5 Verification

After Phase 1, running `docker compose up` must produce identical behavior to the current system. All existing unit and integration tests must pass without modification. The `RedisAgentBackend` is a pure refactor — no new behavior.

**Files modified:**
- `control_plane/core/scheduler.py` (refactor)
- `control_plane/app.py` (backend selection)

**Files created:**
- `control_plane/core/backend.py` (interface)
- `control_plane/core/backends/__init__.py`
- `control_plane/core/backends/redis_agent.py`

**Files unchanged:**
- All API endpoints, models, persistence, frontend, agents, tests

---

## Phase 2: SLURM Backend Implementation

**Goal:** Build a `SlurmBackend` that translates our JobSpec into SLURM operations.

### 2.1 Create SlurmBackend

Create `control_plane/core/backends/slurm.py`:

#### submit() — JobSpec to sbatch

```python
def submit(self, spec: JobSpec, node_hint: str | None = None) -> str:
    script = self._generate_batch_script(spec, node_hint)
    script_path = self._write_temp_script(spec.job_id, script)

    result = subprocess.run(
        ["sbatch", script_path],
        capture_output=True, text=True, timeout=30
    )

    if result.returncode != 0:
        raise SlurmSubmitError(result.stderr)

    slurm_job_id = self._parse_submit_output(result.stdout)
    # "Submitted batch job 12345" → "12345"

    self._store_slurm_mapping(spec.job_id, slurm_job_id)
    return slurm_job_id
```

#### Batch script generation

```python
def _generate_batch_script(self, spec: JobSpec, node_hint: str | None) -> str:
    lines = [
        "#!/bin/bash",
        f"#SBATCH --job-name={spec.job_id}",
        f"#SBATCH --gres=gpu:{spec.gpus}",
        f"#SBATCH --output={self.log_dir}/{spec.job_id}-%j.out",
        f"#SBATCH --error={self.log_dir}/{spec.job_id}-%j.err",
    ]

    if spec.cpu:
        lines.append(f"#SBATCH --cpus-per-task={spec.cpu}")
    if spec.mem_gb:
        lines.append(f"#SBATCH --mem={int(spec.mem_gb * 1024)}M")
    if node_hint:
        lines.append(f"#SBATCH --nodelist={node_hint}")

    # Partition selection based on GPU requirements
    partition = self._select_partition(spec)
    lines.append(f"#SBATCH --partition={partition}")

    # Environment variables
    for key, value in spec.env.items():
        lines.append(f"export {key}={shlex.quote(value)}")

    # State callback: notify our control plane when job starts/ends
    callback_url = f"{self.control_plane_url}/api/admin/jobs/{spec.job_id}/state"
    lines.append("")
    lines.append(f'curl -s -X POST {callback_url} -H "Content-Type: application/json" '
                 f'-H "Authorization: Bearer {self.agent_token}" '
                 f'-d \'{{"state": "RUNNING"}}\'')
    lines.append("")

    # Actual command
    if spec.image:
        lines.append(f"apptainer exec --nv {spec.image} {shlex.join(spec.cmd)}")
    else:
        lines.append(shlex.join(spec.cmd))

    # Report completion
    lines.append("")
    lines.append("EXIT_CODE=$?")
    lines.append(f'if [ $EXIT_CODE -eq 0 ]; then STATE="DONE"; else STATE="FAILED"; fi')
    lines.append(f'curl -s -X POST {callback_url} -H "Content-Type: application/json" '
                 f'-H "Authorization: Bearer {self.agent_token}" '
                 f'-d "{{\\"state\\": \\"$STATE\\", \\"exit_code\\": $EXIT_CODE}}"')

    return "\n".join(lines)
```

#### poll_status() — sacct queries

```python
def poll_status(self, job_id: str) -> JobStatus | None:
    slurm_id = self._get_slurm_mapping(job_id)
    if not slurm_id:
        return None

    result = subprocess.run(
        ["sacct", "-j", slurm_id, "--parsable2", "--noheader",
         "--format=State,ExitCode,Start,End,NodeList"],
        capture_output=True, text=True, timeout=10
    )

    state, exit_code, start, end, nodelist = self._parse_sacct(result.stdout)
    return JobStatus(
        state=self._map_slurm_state(state),
        node_id=nodelist,
        exit_code=exit_code,
        timestamps=self._build_timestamps(start, end),
    )
```

SLURM state mapping:

| SLURM State | Our JobState |
|---|---|
| PENDING | QUEUED |
| RUNNING | RUNNING |
| COMPLETED | DONE |
| FAILED | FAILED |
| CANCELLED | CANCELLED |
| TIMEOUT | FAILED |
| NODE_FAIL | FAILED |

#### list_nodes() — sinfo queries

```python
def list_nodes(self) -> list[NodeInfo]:
    result = subprocess.run(
        ["sinfo", "--json"],
        capture_output=True, text=True, timeout=10
    )

    sinfo = json.loads(result.stdout)
    nodes = []
    for node in sinfo.get("nodes", []):
        gpus = self._parse_gres(node.get("gres", ""))
        nodes.append(NodeInfo(
            node_id=node["name"],
            gpus=gpus,
            labels={
                "partition": ",".join(node.get("partitions", [])),
                "state": node.get("state", "unknown"),
            },
            agent_health={"heartbeat_ts": time.time()},
            last_seen=time.time(),
        ))
    return nodes
```

#### cancel() — scancel

```python
def cancel(self, job_id: str) -> bool:
    slurm_id = self._get_slurm_mapping(job_id)
    if not slurm_id:
        return False
    result = subprocess.run(["scancel", slurm_id], capture_output=True, timeout=10)
    return result.returncode == 0
```

### 2.2 SLURM Job ID Mapping

Add a column to the `jobs` table:

```sql
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS backend_ref TEXT;
-- Stores the SLURM job ID (e.g., "12345") for mapping between our job_id and SLURM's
```

Persistence helpers:

```python
def store_backend_ref(job_id: str, backend_ref: str):
    with pg_conn() as conn:
        conn.execute(
            "UPDATE jobs SET backend_ref = %s WHERE job_id = %s",
            (backend_ref, job_id)
        )

def get_backend_ref(job_id: str) -> str | None:
    with pg_conn() as conn:
        row = conn.execute(
            "SELECT backend_ref FROM jobs WHERE job_id = %s", (job_id,)
        ).fetchone()
        return row[0] if row else None
```

### 2.3 SLURM State Poller

The batch script includes `curl` callbacks for state updates (RUNNING, DONE, FAILED), which reuse our existing `/api/admin/jobs/{job_id}/state` endpoint. This means Postgres is updated through the same path as the agent backend.

As a fallback (in case curl fails inside the job), add a background poller:

```python
def _slurm_poller_loop(self):
    """
    Background thread that polls sacct for jobs that might have
    missed their curl callback (network issues, job killed, etc.)
    """
    while True:
        active_jobs = get_jobs_in_states(["QUEUED", "PLACED", "RUNNING"])
        for job in active_jobs:
            if job.backend_ref:
                status = self.poll_status(job.job_id)
                if status and status.state != job.state:
                    set_job_state(job.job_id, status.state,
                                 status.exit_code, status.reason)
        time.sleep(15)  # Poll every 15 seconds
```

### 2.4 Partition Selection Logic

SJSU's HPC has multiple GPU partitions. The backend should select the right one:

```python
def _select_partition(self, spec: JobSpec) -> str:
    """
    Select the best SLURM partition based on job requirements.
    Can be extended to consider queue wait times, GPU type preferences, etc.
    """
    preferred = spec.metadata.get("partition")
    if preferred:
        return preferred

    # Default: use the general gpu partition
    return self.default_partition
```

This is intentionally simple. Future work can make this smarter (e.g., choose A100 vs H100 based on memory requirements, or pick the partition with the shortest queue).

**Files created:**
- `control_plane/core/backends/slurm.py`

**Files modified:**
- `control_plane/db/schema.sql` (add `backend_ref` column)
- `control_plane/core/persistence.py` (add `store_backend_ref`, `get_backend_ref`)

---

## Phase 3: HPC Deployment and Testing

**Goal:** Run the system on SJSU's HPC with real GPU jobs.

### 3.1 Deployment Model on HPC

On the HPC, the control plane runs as a user process (not a Docker container):

```bash
# On HPC login node (or within a long-running interactive job)
cd ~/CudaAwareDistributedScheduler

# Option A: Use SQLite instead of Postgres (zero infrastructure)
export DATABASE_URL="sqlite:///~/scheduler.db"

# Option B: Use a Postgres instance if available
export DATABASE_URL="postgresql://..."

# Redis can be replaced with in-memory queue for SLURM mode
export BACKEND=slurm
export SLURM_LOG_DIR=~/scheduler-logs
export AUTH_MODE=token
export OPERATOR_API_TOKEN=<generate-a-token>

# Start the control plane
python -m uvicorn control_plane.app:app --host 0.0.0.0 --port 8000
```

### 3.2 Optional: SQLite Backend for Zero Infrastructure

For HPC deployment, requiring Postgres and Redis is a burden. Consider a lightweight persistence option:

- Replace Postgres with **SQLite** for the jobs/nodes/events tables (single file, no server process)
- Replace Redis job queue with a **Python `queue.Queue`** or an SQLite-backed queue (the scheduler runs in-process anyway)
- Redis spec cache becomes an in-memory dict

This is optional but would make HPC deployment trivial — a single Python process with a single SQLite file.

### 3.3 Test Plan

| Test | Method |
|---|---|
| Submit a simple echo job | `POST /api/jobs` with `cmd: ["echo", "hello"]`, verify it appears in `squeue`, verify DONE state after completion |
| Submit a GPU job | `cmd: ["nvidia-smi"]`, `gpus: 1`, verify `--gres=gpu:1` in the generated batch script |
| Bin-packing validation | Submit multiple jobs, verify BINPACK selects nodes with highest current utilization |
| Cancellation | Submit a long job, call cancel, verify `scancel` is invoked and state becomes CANCELLED |
| Callback failure recovery | Kill curl in the batch script, verify the poller thread picks up the final state from `sacct` |
| Frontend integration | Open dashboard, submit jobs through the UI, verify live status updates |

### 3.4 Partition-Aware Scheduling

Once running on the HPC, extend the BINPACK policy to consider partition-level data:

- Query `squeue` for pending job counts per partition
- Estimate wait times based on queue depth and historical run durations
- Surface wait time estimates in the frontend

---

## Phase 4: Frontend Enhancements

**Goal:** Surface SLURM-specific information in the dashboard.

### 4.1 Partition Selector

Add a partition dropdown to the job submission form:

```typescript
// Jobs.tsx — add partition field
<select name="partition" value={partition} onChange={...}>
  <option value="">Auto-select</option>
  <option value="gpu">GPU (P100)</option>
  <option value="gpu-a100">GPU (A100)</option>
  <option value="gpu-h100">GPU (H100)</option>
</select>
```

The selected partition is passed as `metadata.partition` in the JobSpec.

### 4.2 Queue Wait Estimates

Add a new metrics panel showing estimated wait time per partition:

- Control plane queries `squeue --json` periodically
- Calculates average wait time for pending jobs in each partition
- Frontend displays: "Estimated wait: ~12 min (gpu), ~3 min (gpu-h100)"

### 4.3 SLURM Job ID Display

Show the SLURM job ID alongside our internal job_id in the Jobs table:

```typescript
// Jobs.tsx — add column
<td>{job.backend_ref ? `SLURM #${job.backend_ref}` : "—"}</td>
```

### 4.4 Node View Updates

The Nodes page currently shows agent heartbeat data. With SLURM backend, it shows `sinfo` data instead:

- Node state (idle, allocated, mixed, down)
- Partition membership
- GPU type and count
- Current allocations

The `NodeInfo` model is already flexible enough — the `labels` dict carries partition and state info from `sinfo`.

---

## File Change Summary

| File | Action | Description |
|---|---|---|
| `control_plane/core/backend.py` | **Create** | ExecutionBackend abstract class |
| `control_plane/core/backends/__init__.py` | **Create** | Package init |
| `control_plane/core/backends/redis_agent.py` | **Create** | Wraps existing Redis+Agent dispatch |
| `control_plane/core/backends/slurm.py` | **Create** | SLURM sbatch/sacct/sinfo integration |
| `control_plane/core/scheduler.py` | **Modify** | Accept backend in constructor, delegate dispatch |
| `control_plane/core/persistence.py` | **Modify** | Add backend_ref helpers |
| `control_plane/app.py` | **Modify** | Backend selection from env var |
| `control_plane/db/schema.sql` | **Modify** | Add backend_ref column to jobs table |
| `frontend/src/pages/Jobs.tsx` | **Modify** | Show SLURM job ID, partition selector |
| `frontend/src/pages/Nodes.tsx` | **Modify** | Show SLURM node state and partitions |
| `frontend/src/api/client.ts` | **Modify** | Add partition field to job submission |
| `agent/*` | **No change** | Kept as RedisAgentBackend option |
| `deploy/docker-compose.yml` | **No change** | Still used for local dev |
| `tests/unit/test_scheduler.py` | **Modify** | Mock backend interface instead of Redis |
| `tests/unit/test_slurm_backend.py` | **Create** | Unit tests for SlurmBackend |

---

## Risk Assessment

| Risk | Mitigation |
|---|---|
| SLURM CLI output format changes between versions | Parse with regex patterns, add version detection |
| HPC login node restrictions on running servers | Run control plane inside an interactive SLURM job, or as a screen/tmux session |
| curl callbacks fail inside SLURM jobs (network restrictions) | Poller thread as fallback, sacct is always available post-completion |
| sinfo/squeue have rate limits or slow response on busy clusters | Cache node data for 10-30 seconds, avoid polling per-tick |
| SQLite concurrent write contention | Use WAL mode, keep write transactions short |

---

## Success Criteria

- [ ] `docker compose up` still works identically (RedisAgentBackend)
- [ ] `BACKEND=slurm` starts the control plane with SlurmBackend
- [ ] Jobs submitted through the API produce valid `sbatch` scripts
- [ ] Job state transitions (QUEUED → RUNNING → DONE/FAILED) are tracked correctly via callbacks and/or sacct polling
- [ ] Frontend shows SLURM job IDs and partition information
- [ ] At least one real GPU job completes successfully on SJSU's HPC
- [ ] All existing unit tests pass with the backend abstraction
- [ ] New unit tests cover SlurmBackend with mocked subprocess calls
