# Milestone 8 Prompt: Security, Observability, and Policy Execution

You are a senior full-stack engineer working on CudaAwareDistributedScheduler, a CUDA-aware overlay scheduler with:
- FastAPI control plane
- Redis queue
- Postgres state
- agent worker + heartbeat runtime
- React/Vite admin UI added in Milestone 7

Before making changes, read these docs to understand the existing system and previous milestones:
- `README.md`
- `docs/project-status-2026-02-15.md`
- `docs/milestone-7-plan.md`
- `docs/milestone-7-prompt.md`
- `docs/milestone-8-plan.md`

Then inspect the current implementation in:
- `control_plane/`
- `agent/`
- `frontend/`
- `tests/`
- `deploy/docker-compose.yml`

Your task is to implement Milestone 8 according to `docs/milestone-8-plan.md`.

Implement:
1. Token auth for mutating control-plane endpoints, with:
   - `AUTH_MODE=none` as backward-compatible default
   - `AUTH_MODE=token` enabling bearer auth
   - operator vs agent token scope enforcement
2. Frontend operator token entry and request wiring
3. `GET /api/metrics/summary` with the documented response contract
4. Policy persistence plus `PUT /api/policies/active`
5. Real scheduler behavior for `FIFO`, `ROUND_ROBIN`, and Milestone 8 `BINPACK` exactly as documented
6. Documentation updates needed for local dev / compose auth and metrics flow
7. Unit and integration tests for auth, metrics, and policy behavior

Constraints:
1. Follow the existing prototype architecture; do not introduce OAuth, RBAC, Prometheus, or a migration framework
2. Keep the implementation pragmatic and small
3. Do not silently change documented API contracts
4. Preserve Milestone 7 usability
5. Respect the plan’s stated caveats, especially around Redis security and inventory-based BINPACK

After implementation:
1. Run the relevant build, lint, and test commands
2. Run local smoke checks if possible
3. Do a final code review of your own changes
4. Report findings first: bugs, gaps, regressions, contract mismatches, missing docs/tests, and anything unverified
5. If there are no findings, say that explicitly and note residual risks

Deliver the implementation and the final review in the same run.
