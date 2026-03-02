import os
import time

import pytest
import requests

API_BASE = os.getenv("OVERLAY_API_BASE", "http://localhost:8000")
OPERATOR_TOKEN = os.getenv("OVERLAY_OPERATOR_TOKEN", "local-operator-token")
AGENT_TOKEN = os.getenv("OVERLAY_AGENT_TOKEN", "local-agent-token")

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


def _require_integration():
    if os.getenv("RUN_INTEGRATION") != "1":
        pytest.skip("set RUN_INTEGRATION=1 to run lifecycle integration tests")
    _wait_for_ready()


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_unauthenticated_mutations_are_rejected():
    _require_integration()

    job_response = requests.post(
        f"{API_BASE}/api/jobs",
        json={"job_id": f"unauth-job-{int(time.time())}", "image": "", "cmd": ["echo", "unauth"]},
        timeout=5,
    )
    node_response = requests.post(
        f"{API_BASE}/api/nodes",
        json={"node_id": "unauth-node", "gpus": [], "labels": {}, "agent_health": {}},
        timeout=5,
    )

    assert job_response.status_code == 401
    assert node_response.status_code == 401


def test_agent_token_allows_heartbeat_and_operator_token_allows_job_submission():
    _require_integration()

    heartbeat = requests.post(
        f"{API_BASE}/api/nodes",
        json={"node_id": "integration-node", "gpus": [], "labels": {}, "agent_health": {"heartbeat_ts": time.time()}},
        headers=_auth_headers(AGENT_TOKEN),
        timeout=5,
    )
    enqueue = requests.post(
        f"{API_BASE}/api/jobs",
        json={
            "job_id": f"auth-it-{int(time.time())}",
            "image": "",
            "cmd": ["sh", "-c", "sleep 1; echo integration"],
        },
        headers=_auth_headers(OPERATOR_TOKEN),
        timeout=5,
    )

    assert heartbeat.status_code == 202
    assert enqueue.status_code == 201
    assert enqueue.json()["created"] is True


def test_policy_mutation_and_metrics_summary_contract():
    _require_integration()

    update = requests.put(
        f"{API_BASE}/api/policies/active",
        json={"policy": "BINPACK"},
        headers=_auth_headers(OPERATOR_TOKEN),
        timeout=5,
    )
    policies = requests.get(f"{API_BASE}/api/policies", timeout=5)
    metrics = requests.get(f"{API_BASE}/api/metrics/summary", timeout=5)

    assert update.status_code == 200
    assert update.json()["active"] == "BINPACK"
    assert policies.status_code == 200
    assert policies.json()["active"] == "BINPACK"

    assert metrics.status_code == 200
    payload = metrics.json()
    assert payload["queue_depth"] >= 0
    assert payload["window_minutes"] == 60
    assert set(payload["jobs"].keys()) == {
        "queued",
        "placed",
        "running",
        "done",
        "failed",
        "cancelled",
    }
    assert set(payload["nodes"].keys()) == {"total", "fresh", "stale"}
    assert set(payload["latency_ms"].keys()) == {
        "placement_p50",
        "placement_p95",
        "run_p50",
        "run_p95",
    }
    assert set(payload["windowed_terminal_counts"].keys()) == {"done", "failed"}
    assert all(value >= 0 for value in payload["latency_ms"].values())


def test_job_lifecycle_reaches_done():
    _require_integration()

    job_id = f"it-{int(time.time())}"
    enqueue = requests.post(
        f"{API_BASE}/api/jobs",
        json={"job_id": job_id, "image": "", "cmd": ["sh", "-c", "sleep 1; echo integration"]},
        headers=_auth_headers(OPERATOR_TOKEN),
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
