import requests

from agent import worker
from agent.executor import ExecutionResult


class FakeResponse:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text

    @property
    def ok(self):
        return 200 <= self.status_code < 300


class FakeRedis:
    def __init__(self, spec_raw=None):
        self.spec_raw = spec_raw

    def get(self, key):
        assert key.startswith("jobs:spec:")
        return self.spec_raw


def test_post_state_update_retries_until_success(monkeypatch):
    calls = []
    sleep_calls = []
    responses = [
        FakeResponse(500, "server error"),
        FakeResponse(200, "ok"),
    ]

    def fake_post(url, json, headers, timeout):
        calls.append((url, json, headers, timeout))
        return responses.pop(0)

    monkeypatch.setattr(worker.requests, "post", fake_post)
    monkeypatch.setattr(worker.time, "sleep", sleep_calls.append)

    assert worker._post_state_update("job-1", "RUNNING") is True
    assert len(calls) == 2
    assert sleep_calls == [worker.STATE_UPDATE_BASE_DELAY]


def test_post_state_update_handles_request_exception(monkeypatch):
    calls = []
    sleep_calls = []

    def fake_post(url, json, headers, timeout):
        calls.append((url, json, headers, timeout))
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(worker.requests, "post", fake_post)
    monkeypatch.setattr(worker.time, "sleep", sleep_calls.append)
    monkeypatch.setattr(worker, "STATE_UPDATE_ATTEMPTS", 2)
    monkeypatch.setattr(worker, "STATE_UPDATE_BASE_DELAY", 0.1)

    assert worker._post_state_update("job-1", "RUNNING") is False
    assert len(calls) == 2
    assert sleep_calls == [0.1]


def test_process_job_uses_fallback_spec_and_reports_failure_reason(monkeypatch):
    state_calls = []

    def fake_run_job(cmd, image, env):
        assert cmd == ["echo", "job-1"]
        assert image is None
        assert env is None
        return ExecutionResult(
            exit_code=127,
            reason="Image execution requested but 'apptainer' is not installed or not in PATH",
        )

    def fake_post_state(job_id, state, exit_code=None, reason=None):
        state_calls.append((job_id, state, exit_code, reason))
        return True

    monkeypatch.setattr(worker, "r", FakeRedis(spec_raw=None))
    monkeypatch.setattr(worker, "run_job", fake_run_job)
    monkeypatch.setattr(worker, "_post_state_update", fake_post_state)

    worker.process_job("job-1")

    assert state_calls == [
        ("job-1", "RUNNING", None, None),
        (
            "job-1",
            "FAILED",
            127,
            "Image execution requested but 'apptainer' is not installed or not in PATH",
        ),
    ]
