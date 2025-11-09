# CudaAwareDistributedScheduler

# CUDA Overlay — Milestone 1 (Scaffold & Local Compose)

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
