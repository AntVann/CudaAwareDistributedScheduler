import os
import time

import pytest
import requests

API_BASE = os.getenv("OVERLAY_API_BASE", "http://localhost:8000")
ADMIN_TOKEN = os.getenv("OVERLAY_ADMIN_TOKEN", "local-operator-token")
AGENT_TOKEN = os.getenv("OVERLAY_AGENT_TOKEN", "local-agent-token")
TOKEN_DELIVERY_MODE = os.getenv("OVERLAY_TOKEN_DELIVERY_MODE", "email")

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


def _submit_token_request(email: str, projects: list[str], purpose: str = "integration") -> str:
    response = requests.post(
        f"{API_BASE}/api/token-requests",
        json={
            "subject_name": email.split("@")[0],
            "email": email,
            "requested_projects": projects,
            "purpose": purpose,
        },
        timeout=5,
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "PENDING"
    return payload["request_id"]


def _approve_token_request(request_id: str) -> dict:
    response = requests.post(
        f"{API_BASE}/api/admin/token-requests/{request_id}/approve",
        json={"review_notes": "integration approve"},
        headers=_auth_headers(ADMIN_TOKEN),
        timeout=10,
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "APPROVED"
    assert payload["token_id"]
    if TOKEN_DELIVERY_MODE == "response":
        assert payload["plaintext_token"]
    return payload


def _get_token_requests(status: str) -> list[dict]:
    response = requests.get(
        f"{API_BASE}/api/admin/token-requests",
        params={"status": status},
        headers=_auth_headers(ADMIN_TOKEN),
        timeout=5,
    )
    assert response.status_code == 200
    return response.json()


def _get_admin_tokens() -> list[dict]:
    response = requests.get(
        f"{API_BASE}/api/admin/tokens",
        headers=_auth_headers(ADMIN_TOKEN),
        timeout=5,
    )
    assert response.status_code == 200
    return response.json()


def test_public_token_request_submission():
    _require_integration()

    request_id = f"it-user-{int(time.time())}@example.com"
    response = requests.post(
        f"{API_BASE}/api/token-requests",
        json={
            "subject_name": "Integration User",
            "email": request_id,
            "requested_projects": ["default"],
            "purpose": "integration smoke",
        },
        timeout=5,
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "PENDING"


def test_unauthenticated_reads_and_mutations_are_rejected():
    _require_integration()

    endpoints = [
        ("GET", "/api/jobs"),
        ("GET", "/api/nodes"),
        ("GET", "/api/metrics/summary"),
        ("GET", "/api/policies"),
    ]
    for method, path in endpoints:
        resp = requests.request(method, f"{API_BASE}{path}", timeout=5)
        assert resp.status_code == 401

    job_response = requests.post(
        f"{API_BASE}/api/jobs",
        json={"job_id": f"unauth-job-{int(time.time())}", "project": "default", "image": "", "cmd": ["echo", "unauth"]},
        timeout=5,
    )
    node_response = requests.post(
        f"{API_BASE}/api/nodes",
        json={"node_id": "unauth-node", "gpus": [], "labels": {}, "agent_health": {}},
        timeout=5,
    )
    assert job_response.status_code == 401
    assert node_response.status_code == 401


def test_agent_token_allows_heartbeat_and_admin_token_allows_job_submission():
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
            "project": "default",
            "image": "",
            "cmd": ["sh", "-c", "sleep 1; echo integration"],
        },
        headers=_auth_headers(ADMIN_TOKEN),
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
        headers=_auth_headers(ADMIN_TOKEN),
        timeout=5,
    )
    policies = requests.get(f"{API_BASE}/api/policies", headers=_auth_headers(ADMIN_TOKEN), timeout=5)
    metrics = requests.get(f"{API_BASE}/api/metrics/summary", headers=_auth_headers(ADMIN_TOKEN), timeout=5)

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


def test_job_lifecycle_reaches_done():
    _require_integration()

    job_id = f"it-{int(time.time())}"
    enqueue = requests.post(
        f"{API_BASE}/api/jobs",
        json={"job_id": job_id, "project": "default", "image": "", "cmd": ["sh", "-c", "sleep 1; echo integration"]},
        headers=_auth_headers(ADMIN_TOKEN),
        timeout=5,
    )
    assert enqueue.status_code == 201
    assert enqueue.json()["created"] is True

    seen_states = set()
    status = None
    deadline = time.time() + 30
    while time.time() < deadline:
        response = requests.get(f"{API_BASE}/api/jobs/{job_id}", headers=_auth_headers(ADMIN_TOKEN), timeout=5)
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
    assert status["project"] == "default"
    assert {"QUEUED", "RUNNING", "DONE"}.issubset(seen_states)
    assert "enqueued" in status["timestamps"]
    assert "placed" in status["timestamps"]
    assert "running" in status["timestamps"]
    assert "done" in status["timestamps"]


def test_job_logs_endpoint_returns_local_backend_output():
    _require_integration()

    job_id = f"logs-it-{int(time.time())}"
    enqueue = requests.post(
        f"{API_BASE}/api/jobs",
        json={
            "job_id": job_id,
            "project": "default",
            "image": "",
            "cmd": ["sh", "-c", "echo stdout-line; echo stderr-line >&2"],
        },
        headers=_auth_headers(ADMIN_TOKEN),
        timeout=5,
    )
    assert enqueue.status_code == 201

    deadline = time.time() + 30
    while time.time() < deadline:
        response = requests.get(f"{API_BASE}/api/jobs/{job_id}", headers=_auth_headers(ADMIN_TOKEN), timeout=5)
        assert response.status_code == 200
        status = response.json()
        if status["state"] in {"DONE", "FAILED"}:
            break
        time.sleep(0.2)

    assert status["state"] == "DONE"

    stdout_logs = requests.get(
        f"{API_BASE}/api/jobs/{job_id}/logs",
        params={"stream": "stdout", "tail": 50},
        headers=_auth_headers(ADMIN_TOKEN),
        timeout=5,
    )
    stderr_logs = requests.get(
        f"{API_BASE}/api/jobs/{job_id}/logs",
        params={"stream": "stderr", "tail": 50},
        headers=_auth_headers(ADMIN_TOKEN),
        timeout=5,
    )

    assert stdout_logs.status_code == 200
    assert stderr_logs.status_code == 200
    assert "stdout-line" in stdout_logs.json()["content"]
    assert "stderr-line" in stderr_logs.json()["content"]


def test_request_approve_updates_status_and_issues_token():
    _require_integration()

    ts = int(time.time())
    email = f"e2e-user-{ts}@example.com"
    pending_before = _get_token_requests("PENDING")
    request_id = _submit_token_request(email=email, projects=["proj-e2e"], purpose="e2e token flow")
    pending_after = _get_token_requests("PENDING")
    assert any(item["request_id"] == request_id for item in pending_after)
    assert len(pending_after) >= len(pending_before)

    approval = _approve_token_request(request_id)
    token_id = approval["token_id"]
    assert approval["request_id"] == request_id
    assert approval["expires_at"]

    approved_rows = _get_token_requests("APPROVED")
    approved = next(row for row in approved_rows if row["request_id"] == request_id)
    assert approved["status"] == "APPROVED"
    assert approved["reviewed_by"]
    assert "proj-e2e" in approved["requested_projects"]

    tokens = _get_admin_tokens()
    token_row = next(row for row in tokens if row["token_id"] == token_id)
    assert token_row["active"] is True
    assert token_row["role"] == "user"
    assert token_row["subject"] == email.split("@")[0]
    assert "proj-e2e" in token_row["projects"]


def test_two_token_requests_issue_separate_scoped_tokens():
    _require_integration()

    ts = int(time.time())
    email_a = f"user-a-{ts}@example.com"
    email_b = f"user-b-{ts}@example.com"

    req_a = _submit_token_request(email=email_a, projects=["proj-a"], purpose="project a")
    req_b = _submit_token_request(email=email_b, projects=["proj-b"], purpose="project b")

    approve_a = _approve_token_request(req_a)
    approve_b = _approve_token_request(req_b)
    assert approve_a["token_id"] != approve_b["token_id"]

    tokens = _get_admin_tokens()
    token_a = next(row for row in tokens if row["token_id"] == approve_a["token_id"])
    token_b = next(row for row in tokens if row["token_id"] == approve_b["token_id"])

    assert token_a["subject"] == email_a.split("@")[0]
    assert token_b["subject"] == email_b.split("@")[0]
    assert token_a["projects"] == ["proj-a"]
    assert token_b["projects"] == ["proj-b"]
