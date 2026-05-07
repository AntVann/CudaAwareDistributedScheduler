from control_plane.core.backends.redis_agent import RedisAgentBackend
from control_plane.core.models import JobSpec, JobState, JobStatus


class FakeRedis:
    def __init__(self):
        self.calls = []

    def rpush(self, key, value):
        self.calls.append((key, value))
        return 1


def test_submit_stores_backend_ref_and_pushes_assignment(monkeypatch, tmp_path):
    monkeypatch.setenv("JOB_LOG_DIR", str(tmp_path))
    backend = RedisAgentBackend()
    fake_redis = FakeRedis()
    captured = {}

    monkeypatch.setattr("control_plane.core.backends.redis_agent.redis_client", lambda: fake_redis)
    monkeypatch.setattr(
        "control_plane.core.backends.redis_agent.store_backend_ref",
        lambda job_id, backend_ref: captured.update({"job_id": job_id, "backend_ref": backend_ref}),
    )

    spec = JobSpec(job_id="job-1", project="default", image="", cmd=["echo", "hello"], gpus=1)
    result = backend.submit(spec, node_hint="node-a")

    assert result == "job-1"
    assert fake_redis.calls == [("assign:node-a", "job-1")]
    assert captured == {"job_id": "job-1", "backend_ref": "job-1"}


def test_read_logs_returns_tail_for_local_job(monkeypatch, tmp_path):
    monkeypatch.setenv("JOB_LOG_DIR", str(tmp_path))
    backend = RedisAgentBackend()
    stdout_path = tmp_path / "job-1.out"
    stderr_path = tmp_path / "job-1.err"
    stdout_path.write_text("a\nb\nc\n", encoding="utf-8")
    stderr_path.write_text("err-1\nerr-2\n", encoding="utf-8")

    monkeypatch.setattr(
        "control_plane.core.backends.redis_agent.get_job_status",
        lambda job_id: JobStatus(state=JobState.DONE, project="default"),
    )

    result = backend.read_logs("job-1", stream="stdout", tail=2)

    assert result is not None
    assert result["stream"] == "stdout"
    assert result["exists"] is True
    assert result["content"] == "b\nc\n"
    assert result["lines"] == 2
    assert result["truncated"] is True
    assert result["path"].endswith("job-1.out")


def test_read_logs_returns_none_when_job_missing(monkeypatch):
    monkeypatch.setenv("JOB_LOG_DIR", "/tmp/test-job-logs")
    backend = RedisAgentBackend()
    monkeypatch.setattr("control_plane.core.backends.redis_agent.get_job_status", lambda job_id: None)

    assert backend.read_logs("missing-job") is None
