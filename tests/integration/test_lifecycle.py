import os
import time

import pytest
import requests

API_BASE = os.getenv("OVERLAY_API_BASE", "http://localhost:8000")

pytestmark = pytest.mark.integration


def _wait_for_ready(timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = requests.get(f"{API_BASE}/ready", timeout=2)
            if response.ok and response.json().get("ok"):
                return
        except requests.RequestException:
            pass
        time.sleep(1)

    pytest.skip("compose stack is not ready for integration testing")


def test_job_lifecycle_reaches_done():
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("set RUN_INTEGRATION=1 to run lifecycle integration tests")

    _wait_for_ready()

    job_id = f"it-{int(time.time())}"
    enqueue = requests.post(
        f"{API_BASE}/api/jobs",
        json={"job_id": job_id, "image": "", "cmd": ["sh", "-c", "sleep 1; echo integration"]},
        timeout=5,
    )
    assert enqueue.status_code == 201
    assert enqueue.json()["created"] is True

    seen_states = set()
    status = None
    deadline = time.time() + 30
    while time.time() < deadline:
        response = requests.get(f"{API_BASE}/api/jobs/{job_id}", timeout=5)
        assert response.status_code == 200
        status = response.json()
        seen_states.add(status["state"])
        if status["state"] in {"DONE", "FAILED"}:
            break
        time.sleep(0.2)

    assert status is not None
    assert status["state"] == "DONE"
    assert status["exit_code"] == 0
    assert status["node_id"] is not None
    assert {"QUEUED", "RUNNING", "DONE"}.issubset(seen_states)
    assert "enqueued" in status["timestamps"]
    assert "placed" in status["timestamps"]
    assert "running" in status["timestamps"]
    assert "done" in status["timestamps"]
