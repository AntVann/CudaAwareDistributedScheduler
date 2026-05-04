from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

import redis
import requests

from agent.executor import run_job

logger = logging.getLogger("agent.worker")

CONTROL_URL = os.getenv("CONTROL_URL", os.getenv("CONTROL_PLANE_API", "http://control-plane:8000"))
NODE_ID = os.getenv("NODE_ID", "node")
ASSIGN_Q = f"assign:{NODE_ID}"
AGENT_API_TOKEN = os.getenv("AGENT_API_TOKEN", "").strip()

r = redis.Redis(
    host=os.getenv("REDIS_HOST", "redis"),
    port=int(os.getenv("REDIS_PORT", "6379")),
    decode_responses=True,
)

STATE_UPDATE_ATTEMPTS = int(os.getenv("STATE_UPDATE_ATTEMPTS", "3"))
STATE_UPDATE_BASE_DELAY = float(os.getenv("STATE_UPDATE_BASE_DELAY", "0.25"))


def _post_state_update(
    job_id: str,
    state: str,
    exit_code: Optional[int] = None,
    reason: Optional[str] = None,
) -> bool:
    payload: dict[str, Any] = {"state": state}
    if exit_code is not None:
        payload["exit_code"] = exit_code
    if reason:
        payload["reason"] = reason

    url = f"{CONTROL_URL}/api/admin/jobs/{job_id}/state"
    delay = STATE_UPDATE_BASE_DELAY
    for attempt in range(1, STATE_UPDATE_ATTEMPTS + 1):
        try:
            response = requests.post(url, json=payload, headers=_auth_headers(), timeout=5)
            if response.ok:
                return True
            logger.warning(
                "State update failed for job %s to %s (attempt %s/%s, status=%s): %s",
                job_id,
                state,
                attempt,
                STATE_UPDATE_ATTEMPTS,
                response.status_code,
                response.text[:200],
            )
        except requests.RequestException as exc:
            logger.warning(
                "State update request failed for job %s to %s (attempt %s/%s): %s",
                job_id,
                state,
                attempt,
                STATE_UPDATE_ATTEMPTS,
                exc,
            )

        if attempt < STATE_UPDATE_ATTEMPTS:
            time.sleep(delay)
            delay *= 2

    logger.error("Giving up on state update for job %s to %s", job_id, state)
    return False


def _auth_headers() -> dict[str, str]:
    if not AGENT_API_TOKEN:
        return {}
    return {"Authorization": f"Bearer {AGENT_API_TOKEN}"}


def _load_job_spec(job_id: str) -> dict[str, Any]:
    spec_raw = r.get(f"jobs:spec:{job_id}")
    return json.loads(spec_raw) if spec_raw else {"cmd": ["echo", job_id]}


def process_job(job_id: str) -> None:
    spec = _load_job_spec(job_id)
    _post_state_update(job_id, "RUNNING")

    result = run_job(spec.get("cmd", []), spec.get("image"), spec.get("env"))
    new_state = "DONE" if result.exit_code == 0 else "FAILED"
    _post_state_update(
        job_id,
        new_state,
        exit_code=result.exit_code,
        reason=result.reason,
    )


def loop():
    """
    Blocking worker loop that pulls assignments and runs them.
    """
    logger.info("Starting worker loop; waiting on %s", ASSIGN_Q)
    while True:
        job_id = r.blpop(ASSIGN_Q, timeout=2)
        if not job_id:
            continue
        job_id = job_id[1]
        try:
            process_job(job_id)
        except Exception as exc:
            logger.error("Worker error on job %s: %s", job_id, exc)
            time.sleep(0.1)
