# Project Status Report: SLURM Integration Pivot

---

## Original Approach

Our project, CudaAwareDistributedScheduler, set out to build a GPU-aware job scheduler for distributed computing environments. The idea was straightforward: a control plane that tracks GPU inventory across compute nodes and places jobs using intelligent scheduling policies (FIFO, Round-Robin, Bin-Packing).

Over eight milestones we built a working prototype with:

- A FastAPI control plane with token-based authentication
- Per-node agents that heartbeat GPU metrics via NVML and execute jobs
- Redis-based job queues for asynchronous dispatch
- PostgreSQL for durable job state, events, and metrics
- A React admin dashboard for job submission, node monitoring, and policy switching
- Three scheduling policies with a focus on GPU-aware bin-packing

The system works well in our Docker Compose environment, where we control every node and can run our own agents.

## The Problem We Encountered

When we began planning deployment on SJSU's HPC cluster, we realized our architecture conflicts with how the cluster actually operates. The HPC runs SLURM, and SLURM is the sole authority over resource allocation. Specifically:

1. **We cannot bypass SLURM's resource manager.** The highest risk for us is that our agents directly discover GPUs via NVML and pull jobs from Redis. On the HPC, GPU allocation goes through SLURM's `--gres=gpu` flag. We cannot independently claim GPUs.
2. **Node access is managed by SLURM, not by us.** Our heartbeat-based node discovery is irrelevant — SLURM decides which nodes are available and assigns them.
3. **We cannot run persistent services.** Our control plane and agents assume long-running processes. SLURM enforces job time limits (7 days max on GPU partitions) and does not support persistent daemons.
4. **We cannot run Docker.** Our deployment relies on Docker Compose with five services. The HPC does not provide Docker — only Singularity/Apptainer containers for job execution.
5. **We cannot deploy Redis or PostgreSQL as services.** There is no mechanism to run infrastructure databases as persistent services on shared HPC nodes.

This was a planning oversight on our part. We should have investigated the HPC's operational constraints earlier in the project, before building the agent and Redis dispatch layers.

## What We Learned

The core question became: what value can our application add on top of SLURM, rather than competing with it?

SLURM handles resource allocation and job execution well, but it has real gaps:

- Its CLI tools (`squeue`, `sacct`, `sinfo`) are not user-friendly
- It has no built-in web dashboard for operators
- Its scheduling is partition-based and does not support custom placement heuristics easily
- Historical job analytics and latency tracking require external tooling

Our control plane, API, scheduling policies, metrics layer, and frontend are all backend-agnostic — they do not inherently depend on Redis dispatch or custom agents.

## Our Plan Going Forward

We are refactoring the project to introduce an **execution backend abstraction**. The scheduler's placement logic stays intact, but how jobs are dispatched becomes pluggable:

- **`RedisAgentBackend`** — wraps our existing agent-based execution (for local development and demos)
- **`SlurmBackend`** — translates job submissions into `sbatch` calls and polls `sacct`/`sinfo` for status and node inventory

This means we do not start from scratch. The control plane, API, database, scheduling policies, metrics, and frontend all carry forward. The agent code remains as one backend option. We add SLURM as another.

The result is a system that provides a clean web interface and intelligent scheduling on top of SJSU's HPC, using SLURM for what it does best (resource management and execution) while our application handles what SLURM does not (custom GPU-aware placement policies, operator dashboards, and job analytics).

We have documented the detailed migration plan separately and expect to complete the refactor within the next two milestones.
