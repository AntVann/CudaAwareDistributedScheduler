# CudaAwareDistributedScheduler — SLURM Integration Pivot

_Slide deck content for NotebookLM / presentation generation._
_Target: ~8 slides._

---

## Slide 1: Title

**CudaAwareDistributedScheduler**
Pivoting from Standalone GPU Scheduling to SLURM-Integrated Orchestration

- CMPE 295 — Master's Project
- Serhat Gundem
- San Jose State University
- March 2026

---

## Slide 2: What We Built (Milestones 1–8)

**A GPU-aware job scheduler for distributed compute nodes.**

We built a complete working prototype across eight milestones:

- **Control Plane** (FastAPI): Accepts job submissions, tracks state in PostgreSQL, exposes REST APIs with token-based authentication
- **Scheduling Engine**: Three GPU-aware placement policies — FIFO, Round-Robin, and Bin-Packing — that evaluate real-time GPU utilization, memory, and temperature
- **Per-Node Agents**: Heartbeat GPU metrics via NVIDIA's NVML library every 5 seconds, pull assigned jobs from Redis queues, execute via subprocess or Apptainer containers
- **React Admin Dashboard**: Operator-facing UI with live job tracking, node/GPU inventory, queue depth, latency percentiles (P50/P95), and policy switching
- **Infrastructure**: Redis for fast job dispatch queues, PostgreSQL for durable job state and event history, Docker Compose for single-command deployment

The system runs locally with `docker compose up` and demonstrates all three scheduling policies with simulated or real GPU metrics.

---

## Slide 3: Original Architecture

**How the system works today:**

```
  Operator (Browser)
       │
       ▼
  ┌─────────────────────┐
  │   React Frontend    │  Dashboard, Jobs, Nodes pages
  └────────┬────────────┘
           │ REST API
           ▼
  ┌─────────────────────┐
  │   Control Plane     │  FastAPI + Scheduler (FIFO/RR/BINPACK)
  │   (FastAPI)         │  Token auth, metrics, policy management
  └───┬──────────┬──────┘
      │          │
      ▼          ▼
  PostgreSQL    Redis
  (job state)   (job queue + per-node assign queues)
                 │
        ┌────────┴────────┐
        ▼                 ▼
  ┌──────────┐     ┌──────────┐
  │ Agent A  │     │ Agent B  │   NVML GPU metrics
  │ (node-a) │     │ (node-b) │   Heartbeat every 5s
  └──────────┘     └──────────┘   Execute via Apptainer
```

Key assumptions in this design:
- We control every node and can run persistent agent processes
- We can deploy Redis and PostgreSQL as always-on services
- Agents independently discover GPUs and report to the control plane
- Job dispatch happens through Redis queues we manage

---

## Slide 4: The Problem — SJSU's HPC Runs SLURM

**When we tried to deploy on the university's HPC, we hit five hard constraints:**

1. **No persistent services.** SLURM enforces time limits on all jobs (7 days max on GPU nodes). Our control plane and agents assume they run indefinitely.

2. **No Docker.** The HPC supports Singularity/Apptainer only. Our entire deployment is Docker Compose with five services (Redis, Postgres, Control Plane, Agent A, Agent B).

3. **No direct GPU access.** On the HPC, GPUs are allocated through SLURM's `--gres=gpu` flag. Our agents bypass this by discovering GPUs via NVML and self-assigning work.

4. **No infrastructure databases.** We cannot run Redis or PostgreSQL as persistent services on shared compute nodes.

5. **No node-level control.** SLURM decides which nodes are available. Our heartbeat-based node discovery is irrelevant in this environment.

**This was a planning mistake.** We should have investigated the HPC's constraints before building the agent dispatch layer.

---

## Slide 5: The Key Question

**What value can our application add on top of SLURM?**

SLURM is excellent at resource management and job execution. But it has real gaps:

| SLURM Does Well | SLURM Does Not Do |
|---|---|
| Resource allocation (CPU, GPU, memory) | Web-based operator dashboard |
| Fair-share scheduling between users | Custom GPU-aware placement heuristics |
| Job queueing and execution | Real-time job latency analytics (P50/P95) |
| Partition management | User-friendly job submission UI |
| Node health monitoring | Historical trend visualization |
| Time-limit enforcement | Intelligent cross-partition routing |

Our control plane, scheduling policies, metrics layer, and frontend are **backend-agnostic**. They do not fundamentally depend on Redis dispatch or custom agents. We can keep everything that matters and swap the execution layer.

---

## Slide 6: The Solution — Backend Abstraction

**We introduce an ExecutionBackend interface that makes job dispatch pluggable:**

```python
class ExecutionBackend(ABC):
    def submit(self, spec: JobSpec, node_hint: str | None) -> str: ...
    def poll_status(self, job_id: str) -> JobStatus: ...
    def list_nodes(self) -> list[NodeInfo]: ...
    def cancel(self, job_id: str) -> bool: ...
```

**Two implementations:**

| | RedisAgentBackend (existing) | SlurmBackend (new) |
|---|---|---|
| submit() | RPUSH to Redis assign queue | sbatch with --gres=gpu |
| poll_status() | Agent POSTs state back | sacct --parsable2 |
| list_nodes() | Agent heartbeats to Postgres | sinfo --json |
| cancel() | Not yet implemented | scancel {slurm_id} |

**The scheduler logic does not change.** FIFO, Round-Robin, and Bin-Packing still select the target node. The backend determines how that selection is executed.

```python
# Scheduler tick — same logic, pluggable backend
def tick(self):
    job_id = self.queue.pop()
    spec = get_job_spec(job_id)
    nodes = self.backend.list_nodes()           # ← backend provides nodes
    eligible = [n for n in nodes if n.gpus >= spec.gpus]
    target = self._select_node(eligible)         # ← OUR policy logic
    self.backend.submit(spec, node_hint=target)  # ← backend dispatches
```

---

## Slide 7: What We Keep vs. What Changes

**We do not start from scratch.** The majority of our codebase carries forward.

**Stays (no changes):**
- FastAPI control plane and all API endpoints
- PostgreSQL schema (jobs, nodes, events, scheduler_settings)
- Scheduling policies (FIFO, Round-Robin, Bin-Packing)
- Metrics computation and summary API
- Token-based authentication
- React frontend (Dashboard, Jobs, Nodes pages)
- API client and type definitions

**Refactored (minor changes):**
- `scheduler.py` — calls backend interface instead of Redis directly
- `persistence.py` — adds `slurm_job_id` mapping column
- `app.py` — selects backend based on configuration
- Redis — keeps job queue and spec cache, drops per-node assign queues

**New code:**
- `SlurmBackend` class (~200 lines) — sbatch/sacct/sinfo integration
- SLURM state poller thread — maps SLURM job states to our JobState enum
- Batch script generator — converts JobSpec to SBATCH directives

**Archived (kept as alternative backend):**
- Agent heartbeat loop, worker loop, executor, NVML metrics

---

## Slide 8: Roadmap and Timeline

| Phase | Deliverable | Estimated Effort |
|---|---|---|
| **Phase 1: Backend Interface** | Create `ExecutionBackend` ABC, refactor `scheduler.py` to use it, wrap existing code as `RedisAgentBackend` | 2–3 days |
| **Phase 2: SLURM Backend** | Implement `SlurmBackend` with sbatch/sacct/sinfo, add SLURM state poller, batch script generation | 3–4 days |
| **Phase 3: HPC Testing** | Deploy on SJSU HPC, submit real GPU jobs, validate Bin-Packing against live partition data | 2–3 days |
| **Phase 4: Frontend Polish** | Add partition selector, queue wait estimates, SLURM-specific metrics panels | 2–3 days |

**Key outcomes:**
- Local development still works with `docker compose up` (RedisAgentBackend)
- HPC deployment uses SlurmBackend with minimal infrastructure (only needs Python + Postgres or SQLite)
- Scheduling policies are validated against real GPU workloads on H100/A100 nodes
- Operators get a web dashboard that SLURM does not provide natively
