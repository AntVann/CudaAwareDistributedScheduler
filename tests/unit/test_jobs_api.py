"""Tests for the FastAPI handlers in control_plane.api.jobs.

These call the handler functions directly (bypassing FastAPI's Depends wiring)
so we can monkeypatch persistence + backend without spinning up the full app.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from control_plane.api import jobs as jobs_api
from control_plane.api.auth import AuthPrincipal
from control_plane.core.models import JobState, JobStatus


def _admin() -> AuthPrincipal:
    return AuthPrincipal(
        token_id="t1", subject="tester", role="admin", projects=["*"], expires_at=None
    )


def _request_with_backend(backend) -> SimpleNamespace:
    scheduler = SimpleNamespace(backend=backend)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(scheduler=scheduler)))


def _patch_persistence(monkeypatch, *, status_before, project="default", set_state_calls=None):
    """Wire the persistence functions used by cancel_job."""
    monkeypatch.setattr(jobs_api, "get_job_status", lambda job_id: status_before.get(job_id))
    monkeypatch.setattr(jobs_api, "get_job_project", lambda job_id: project)
    if set_state_calls is None:
        set_state_calls = []
    monkeypatch.setattr(
        jobs_api,
        "set_job_state",
        lambda job_id, state, exit_code=None, reason=None, timestamps=None: set_state_calls.append(
            (job_id, state, reason)
        ),
    )
    return set_state_calls


def test_cancel_queued_job_marks_cancelled_without_calling_backend(monkeypatch):
    """QUEUED jobs have no backend_ref yet; cancelling must not call backend.cancel."""
    backend_calls: list[str] = []

    class TrackingBackend:
        def cancel(self, job_id):
            backend_calls.append(job_id)
            return False  # would normally cause 502, but must not be called

    state_calls = _patch_persistence(
        monkeypatch,
        status_before={
            "j1": JobStatus(state=JobState.QUEUED, project="default"),
            # Status after set_job_state — used by the final get_job_status call.
        },
    )
    # The handler also re-reads after the update; return CANCELLED.
    after = JobStatus(state=JobState.CANCELLED, reason="cancelled by operator", project="default")
    monkeypatch.setattr(
        jobs_api,
        "get_job_status",
        lambda job_id: after if state_calls else JobStatus(state=JobState.QUEUED, project="default"),
    )

    request = _request_with_backend(TrackingBackend())
    result = jobs_api.cancel_job("j1", request, principal=_admin())

    assert backend_calls == []
    assert state_calls == [("j1", "CANCELLED", "cancelled by operator")]
    assert result.state == JobState.CANCELLED


def test_cancel_running_job_returns_502_when_backend_rejects(monkeypatch):
    """If backend.cancel returns False on a PLACED/RUNNING job, the API must
    NOT mutate state — otherwise the dashboard would lie about a still-running job."""
    state_calls = _patch_persistence(
        monkeypatch,
        status_before={"j2": JobStatus(state=JobState.RUNNING, project="default")},
    )

    class RejectingBackend:
        def cancel(self, job_id):
            return False

    request = _request_with_backend(RejectingBackend())

    with pytest.raises(HTTPException) as excinfo:
        jobs_api.cancel_job("j2", request, principal=_admin())
    assert excinfo.value.status_code == 502
    assert "rejected" in excinfo.value.detail.lower()
    assert state_calls == []  # state UNCHANGED


def test_cancel_running_job_returns_502_when_backend_raises(monkeypatch):
    state_calls = _patch_persistence(
        monkeypatch,
        status_before={"j3": JobStatus(state=JobState.PLACED, project="default")},
    )

    class ExplodingBackend:
        def cancel(self, job_id):
            raise RuntimeError("scancel: timeout")

    request = _request_with_backend(ExplodingBackend())

    with pytest.raises(HTTPException) as excinfo:
        jobs_api.cancel_job("j3", request, principal=_admin())
    assert excinfo.value.status_code == 502
    assert state_calls == []


def test_cancel_running_job_marks_cancelled_when_backend_accepts(monkeypatch):
    """The happy path for PLACED/RUNNING."""
    state_calls: list = []
    monkeypatch.setattr(
        jobs_api,
        "get_job_status",
        lambda job_id: JobStatus(state=JobState.CANCELLED, reason="cancelled by operator", project="default")
        if state_calls
        else JobStatus(state=JobState.RUNNING, project="default"),
    )
    monkeypatch.setattr(jobs_api, "get_job_project", lambda job_id: "default")
    monkeypatch.setattr(
        jobs_api,
        "set_job_state",
        lambda job_id, state, exit_code=None, reason=None, timestamps=None: state_calls.append(
            (job_id, state, reason)
        ),
    )

    class AcceptingBackend:
        def cancel(self, job_id):
            return True

    request = _request_with_backend(AcceptingBackend())

    result = jobs_api.cancel_job("j4", request, principal=_admin())
    assert state_calls == [("j4", "CANCELLED", "cancelled by operator")]
    assert result.state == JobState.CANCELLED


def test_cancel_terminal_state_returns_409(monkeypatch):
    _patch_persistence(
        monkeypatch,
        status_before={"j5": JobStatus(state=JobState.DONE, exit_code=0, project="default")},
    )
    request = _request_with_backend(SimpleNamespace(cancel=lambda job_id: True))

    with pytest.raises(HTTPException) as excinfo:
        jobs_api.cancel_job("j5", request, principal=_admin())
    assert excinfo.value.status_code == 409


def test_read_job_logs_returns_backend_payload(monkeypatch):
    monkeypatch.setattr(
        jobs_api,
        "get_job_status",
        lambda job_id: JobStatus(state=JobState.DONE, project="default"),
    )
    monkeypatch.setattr(jobs_api, "get_job_project", lambda job_id: "default")

    class LogsBackend:
        def read_logs(self, job_id, stream="stderr", tail=200):
            return {
                "stream": stream,
                "path": f"/shared/logs/{job_id}.err",
                "exists": True,
                "content": "hello\n",
                "lines": 1,
                "bytes_total": 6,
                "truncated": False,
            }

    request = _request_with_backend(LogsBackend())
    payload = jobs_api.read_job_logs("j6", request, stream="stderr", tail=200, principal=_admin())

    assert payload["stream"] == "stderr"
    assert payload["content"] == "hello\n"


def test_read_job_logs_returns_501_without_backend_support(monkeypatch):
    monkeypatch.setattr(
        jobs_api,
        "get_job_status",
        lambda job_id: JobStatus(state=JobState.DONE, project="default"),
    )
    monkeypatch.setattr(jobs_api, "get_job_project", lambda job_id: "default")
    request = _request_with_backend(SimpleNamespace())

    with pytest.raises(HTTPException) as excinfo:
        jobs_api.read_job_logs("j7", request, stream="stderr", tail=200, principal=_admin())
    assert excinfo.value.status_code == 501
