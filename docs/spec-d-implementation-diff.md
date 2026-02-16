# Spec D — Agent Worker & Execution Lifecycle: Implementation Diff

This document compares **Spec D (Milestone 4)** requirements against the current codebase implementation.

---

## Summary

| Section | Status | Notes |
|---------|--------|-------|
| Scope (Redis BLPOP) | **DIFFERENT** | Uses HTTP polling instead of direct Redis BLPOP |
| Redis/HTTP Interactions | **PARTIAL** | Agent uses HTTP; control plane uses Redis internally |
| Worker Loop | **IMPLEMENTED** | Functionally equivalent via HTTP polling |
| run_fake | **IMPLEMENTED** | Called `_execute_job()` with simulation mode |
| State Machine | **IMPLEMENTED** | Full QUEUED→PLACED→RUNNING→DONE/FAILED |
| Error Handling | **PARTIAL** | Missing exponential backoff retry |
| Testing | **NOT IMPLEMENTED** | No unit/integration/soak tests exist |
| Logging | **IMPLEMENTED** | Structured logs with job_id, node_id, exit_code |

---

## Detailed Comparison

### 1. Scope — Redis Consumption Pattern

**Spec:**
```python
# Agent directly uses Redis BLPOP
r = redis.Redis(host=os.getenv("REDIS_HOST","redis"), port=int(os.getenv("REDIS_PORT","6379")), decode_responses=True)
pair = r.blpop(f"assign:{NODE_ID}", timeout=2)
```

**Current Implementation:**
```python
# Agent uses HTTP polling to control plane (agent/agent.py:164-168)
resp = requests.post(
    f"{CONTROL_PLANE_API}/api/nodes/{NODE_ID}/assignments/next",
    timeout=HEARTBEAT_INTERVAL,
)
```

**Diff:**
- Agent does NOT directly connect to Redis
- Agent polls control plane via HTTP POST `/api/nodes/{node_id}/assignments/next`
- Control plane internally does `redis.lpop(assign:{node_id})` (not BLPOP)

**Impact:** Architectural difference - decouples agent from Redis, adds latency but improves security/abstraction.

---

### 2. Redis GET for Job Spec

**Spec:**
```python
spec = json.loads(r.get(f"jobs:spec:{job_id}") or "{}")
```

**Current Implementation:**
- Agent receives spec directly in HTTP response from `/api/nodes/{node_id}/assignments/next`
- Control plane fetches from Redis: `persistence.get_job_spec(job_id)` → `r.get(jobs:spec:{job_id})`

**Diff:** Agent does not directly access Redis for job specs. The control plane fetches and returns it in the `JobAssignment` response.

---

### 3. HTTP POST for State Updates

**Spec:**
```python
requests.post(f"{CONTROL_URL}/api/admin/jobs/{job_id}/state", json={"state":"RUNNING"}, timeout=3)
requests.post(f"{CONTROL_URL}/api/admin/jobs/{job_id}/state", json={"state":"DONE" if rc==0 else "FAILED", "exit_code": rc}, timeout=3)
```

**Current Implementation:**
```python
# Start (agent/agent.py:264-268)
requests.post(f"{CONTROL_PLANE_API}/api/jobs/{job_id}/start", json=payload, timeout=10)

# Finish (agent/agent.py:288-292)
requests.post(f"{CONTROL_PLANE_API}/api/jobs/{job_id}/finish", json=payload, timeout=10)
```

**Diff:**
| Spec | Current | Notes |
|------|---------|-------|
| `/api/admin/jobs/{job_id}/state` | `/api/jobs/{job_id}/start` | Different endpoint pattern |
| `{"state":"RUNNING"}` | `{"node_id":"...", "gpu_ids":[...]}` | Richer payload |
| `/api/admin/jobs/{job_id}/state` | `/api/jobs/{job_id}/finish` | Separate endpoint for finish |
| `{"state":"DONE", "exit_code":0}` | `{"node_id":"...", "success":true, "exit_code":0, "reason":null}` | Richer payload |

**Impact:** Semantically equivalent. Current implementation uses two dedicated endpoints instead of one generic state endpoint.

---

### 4. Worker Loop

**Spec:**
```python
def worker_loop():
    while True:
        pair = r.blpop(f"assign:{NODE_ID}", timeout=2)
        if not pair:
            continue
        job_id = pair[1]
        spec = json.loads(r.get(f"jobs:spec:{job_id}") or "{}")
        requests.post(f"{CONTROL_URL}/api/admin/jobs/{job_id}/state", json={"state":"RUNNING"}, timeout=3)
        rc = run_fake(job_id, spec.get("cmd", ["echo", job_id]), spec.get("image"))
        requests.post(f"{CONTROL_URL}/api/admin/jobs/{job_id}/state",
                      json={"state":"DONE" if rc==0 else "FAILED", "exit_code": rc}, timeout=3)
```

**Current Implementation (agent/agent.py:157-206):**
```python
def _assignment_loop():
    while not _worker_stop_event.is_set():
        try:
            resp = requests.post(f"{CONTROL_PLANE_API}/api/nodes/{NODE_ID}/assignments/next", timeout=HEARTBEAT_INTERVAL)
        except Exception as exc:
            _worker_stop_event.wait(ASSIGNMENT_POLL_INTERVAL)
            continue
        if resp.status_code == 204:
            _worker_stop_event.wait(ASSIGNMENT_POLL_INTERVAL)
            continue
        payload = resp.json()
        _process_assignment(payload)

def _process_assignment(payload: dict):
    job_id = payload.get("job_id")
    spec = payload.get("spec") or {}
    gpu_ids = _allocate_gpu_ids(spec)
    _notify_job_start(job_id, gpu_ids)                    # POST /api/jobs/{job_id}/start
    exit_code, reason = _execute_job(job_id, spec, gpu_ids)
    success = exit_code == 0
    _notify_job_finish(job_id, success, exit_code, reason)  # POST /api/jobs/{job_id}/finish
```

**Diff:**
- Uses HTTP polling instead of Redis BLPOP (2s poll interval vs 2s BLPOP timeout)
- Has graceful shutdown via `_worker_stop_event`
- Adds GPU allocation logic
- Returns reason string in addition to exit_code

---

### 5. run_fake / Fake Execution

**Spec:**
```python
def run_fake(job_id: str, cmd: list[str], image: str | None = None) -> int:
    time.sleep(2)
    return 0
```

**Current Implementation (agent/agent.py:232-241):**
```python
if not _has_real_gpu():
    duration = metadata.get("sim_seconds")
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        duration = SIMULATED_RUN_SECONDS  # default 3s
    duration = max(0.5, duration)
    logger.info("Simulating job %s for %ss (%s)", job_id, duration, cmd)
    sleep(duration)
    return 0, None
```

**Diff:**
- Simulation duration is configurable via `SIMULATED_RUN_SECONDS` env var (default 3s vs spec's 2s)
- Can override per-job via `metadata.sim_seconds`
- Also supports **real execution** when GPU detected (lines 243-258)
- Returns tuple `(exit_code, reason)` instead of just `int`

---

### 6. State Machine

**Spec:**
```
QUEUED (enqueue) → PLACED (scheduler) → RUNNING (agent sets) → DONE|FAILED (agent sets)
```

**Current Implementation:**
```python
# control_plane/core/models.py:6-12
class JobState(str, Enum):
    QUEUED = "QUEUED"      # enqueue_job() sets this
    PLACED = "PLACED"      # scheduler._run() sets this
    RUNNING = "RUNNING"    # agent via /api/jobs/{id}/start
    DONE = "DONE"          # agent via /api/jobs/{id}/finish (success=True)
    FAILED = "FAILED"      # agent via /api/jobs/{id}/finish (success=False)
    CANCELLED = "CANCELLED"
```

**Diff:** IMPLEMENTED + added CANCELLED state (not in spec).

**Timestamps:**
```python
# persistence.py:219-220 - timestamps appended per transition
ts_key = state.value.lower()  # e.g., "running"
timestamps[ts_key] = time.time()
```
IMPLEMENTED as specified.

---

### 7. Error Handling

**Spec:**
> If the state POST fails (control-plane down), retry with exponential backoff up to N times; log and continue.
> If fake exec raises, treat as FAILED with exit_code=1.

**Current Implementation:**

**State POST failures (agent/agent.py:269-277, 293-301):**
```python
try:
    resp = requests.post(f"{CONTROL_PLANE_API}/api/jobs/{job_id}/start", json=payload, timeout=10)
    if resp.status_code >= 400:
        logger.warning("Failed to ack start of %s (%s): %s", job_id, resp.status_code, resp.text[:200])
except Exception as exc:
    logger.warning("Failed to notify start for job %s: %s", job_id, exc)
```

**Diff:**
- **NO exponential backoff** - just logs warning and continues
- No retry attempts

**Execution failure handling (agent/agent.py:253-258):**
```python
except FileNotFoundError as exc:
    logger.error("Command not found for job %s: %s", job_id, exc)
    return 1, f"Command not found: {exc}"
except Exception as exc:
    logger.error("Execution failed for job %s: %s", job_id, exc)
    return 1, f"Execution failed: {exc}"
```

**Diff:** IMPLEMENTED - exceptions return exit_code=1 with reason.

---

### 8. Testing

**Spec:**
> - Unit: worker handles missing jobs:spec gracefully (fallback cmd).
> - Integration: enqueue a job; job transitions through all states within ~5s on a CPU-only machine.
> - Soak: 100 jobs, 2 agents; verify no stuck PLACED jobs after 60s.

**Current Implementation:**
```
tests/
└── __init__.py  (empty)
```

**Diff:** **NO TESTS IMPLEMENTED**
- No unit tests for worker
- No integration tests for job lifecycle
- No soak tests

---

### 9. Logging

**Spec:**
> Every job: at least 3 structured logs: picked, running, completed with job_id, node_id, and exit_code.

**Current Implementation (agent/agent.py):**
```python
# Line 202 - picked/claimed
logger.info("Claimed job %s with spec %s", job_id, spec)

# Line 239 - running (simulation)
logger.info("Simulating job %s for %ss (%s)", job_id, duration, cmd)
# Line 243 - running (real)
logger.info("Executing job %s locally (cmd=%s, image=%s)", job_id, cmd, spec.get("image"))

# Completion is logged via warning on failure only (lines 271, 295)
# No explicit "completed" log with exit_code
```

**Diff:**
- Has "picked" log ✅
- Has "running" log ✅
- **Missing explicit "completed" log with exit_code** - only logs on failure

---

## Gap Summary

### Must Fix (to match spec):

1. **Error Handling - Exponential Backoff**
   - Location: `agent/agent.py` functions `_notify_job_start()` and `_notify_job_finish()`
   - Add retry with exponential backoff (e.g., 1s, 2s, 4s, up to 3 retries)

2. **Logging - Completion Log**
   - Location: `agent/agent.py` function `_process_assignment()`
   - Add: `logger.info("Completed job %s on node %s with exit_code=%d", job_id, NODE_ID, exit_code)`

3. **Testing - All Missing**
   - Create `tests/test_agent_worker.py` for unit tests
   - Create `tests/test_job_lifecycle.py` for integration tests
   - Create `tests/test_soak.py` for soak tests

### Acceptable Deviations:

1. **HTTP Polling vs Redis BLPOP** - Architectural choice that improves security by not exposing Redis to agents. Functionally equivalent.

2. **Separate endpoints vs single state endpoint** - `/api/jobs/{id}/start` and `/api/jobs/{id}/finish` instead of `/api/admin/jobs/{id}/state`. More RESTful, same effect.

3. **Configurable simulation duration** - 3s default vs 2s in spec. Configurable via env var.

4. **Additional CANCELLED state** - Extra state in enum, doesn't break spec compliance.

---

## Recommended Changes

```diff
# agent/agent.py - Add completion logging

def _process_assignment(payload: dict):
    job_id = payload.get("job_id")
    spec = payload.get("spec") or {}
    if not job_id:
        logger.warning("Assignment payload missing job_id: %s", payload)
        return
    gpu_ids = _allocate_gpu_ids(spec)
    logger.info("Claimed job %s with spec %s", job_id, spec)
    _notify_job_start(job_id, gpu_ids)
    exit_code, reason = _execute_job(job_id, spec, gpu_ids)
    success = exit_code == 0
    _notify_job_finish(job_id, success, exit_code, reason)
+   logger.info("Completed job %s on node %s with exit_code=%d", job_id, NODE_ID, exit_code)
```

```diff
# agent/agent.py - Add exponential backoff for state notifications

+import time as time_module  # avoid conflict with existing 'from time import sleep, time'
+
+MAX_RETRIES = 3
+BASE_DELAY = 1.0
+
+def _post_with_retry(url: str, payload: dict, action: str, job_id: str):
+    """POST with exponential backoff retry."""
+    for attempt in range(MAX_RETRIES):
+        try:
+            resp = requests.post(url, json=payload, timeout=10)
+            if resp.status_code < 400:
+                return True
+            logger.warning(
+                "Failed to %s job %s (attempt %d/%d, status=%d): %s",
+                action, job_id, attempt + 1, MAX_RETRIES, resp.status_code, resp.text[:200]
+            )
+        except Exception as exc:
+            logger.warning(
+                "Failed to %s job %s (attempt %d/%d): %s",
+                action, job_id, attempt + 1, MAX_RETRIES, exc
+            )
+        if attempt < MAX_RETRIES - 1:
+            delay = BASE_DELAY * (2 ** attempt)
+            time_module.sleep(delay)
+    return False

def _notify_job_start(job_id: str, gpu_ids: list[int]):
    payload = {"node_id": NODE_ID, "gpu_ids": gpu_ids}
-   try:
-       resp = requests.post(
-           f"{CONTROL_PLANE_API}/api/jobs/{job_id}/start",
-           json=payload,
-           timeout=10,
-       )
-       if resp.status_code >= 400:
-           logger.warning(...)
-   except Exception as exc:
-       logger.warning(...)
+   _post_with_retry(
+       f"{CONTROL_PLANE_API}/api/jobs/{job_id}/start",
+       payload, "start", job_id
+   )

def _notify_job_finish(job_id: str, success: bool, exit_code: int, reason: Optional[str]):
    payload = {"node_id": NODE_ID, "success": success, "exit_code": exit_code, "reason": reason}
-   try:
-       resp = requests.post(...)
-       ...
-   except Exception as exc:
-       logger.warning(...)
+   _post_with_retry(
+       f"{CONTROL_PLANE_API}/api/jobs/{job_id}/finish",
+       payload, "finish", job_id
+   )
```

### Test File Skeletons Needed

```python
# tests/test_agent_worker.py
def test_worker_handles_missing_spec():
    """Worker handles missing jobs:spec gracefully with fallback cmd."""
    pass

# tests/test_job_lifecycle.py
def test_job_transitions_all_states():
    """Enqueue a job; verify it transitions QUEUED→PLACED→RUNNING→DONE within ~5s."""
    pass

# tests/test_soak.py
def test_100_jobs_2_agents_no_stuck():
    """Submit 100 jobs with 2 agents; verify no stuck PLACED jobs after 60s."""
    pass
```
