import json
import logging
import os
import time

import redis
import requests

from agent.executor import run_fake

logger = logging.getLogger("agent.worker")

CONTROL_URL = os.getenv("CONTROL_URL", os.getenv("CONTROL_PLANE_API", "http://control-plane:8000"))
NODE_ID = os.getenv("NODE_ID", "node")
ASSIGN_Q = f"assign:{NODE_ID}"

r = redis.Redis(
    host=os.getenv("REDIS_HOST", "redis"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    decode_responses=True,
)


def loop():
    """
    Blocking worker loop that pulls assignments and runs them (fake).
    """
    logger.info("Starting worker loop; waiting on %s", ASSIGN_Q)
    while True:
        job_id = r.blpop(ASSIGN_Q, timeout=2)
        if not job_id:
            continue
        job_id = job_id[1]
        try:
            spec_raw = r.get(f"jobs:spec:{job_id}")
            spec = json.loads(spec_raw) if spec_raw else {"cmd": ["echo", job_id]}

            # mark RUNNING
            requests.post(
                f"{CONTROL_URL}/api/admin/jobs/{job_id}/state",
                json={"state": "RUNNING"},
                timeout=5,
            )

            rc = run_fake(job_id, spec.get("cmd", []), spec.get("image"))
            new_state = "DONE" if rc == 0 else "FAILED"

            requests.post(
                f"{CONTROL_URL}/api/admin/jobs/{job_id}/state",
                json={"state": new_state, "exit_code": rc},
                timeout=5,
            )
        except Exception as exc:
            logger.error("Worker error on job %s: %s", job_id, exc)
            time.sleep(0.1)
