from control_plane.core import persistence
from control_plane.core.models import JobSpec, JobState, NodeInfo, SchedulerPolicy


def _configure_sqlite(monkeypatch, tmp_path):
    db_path = tmp_path / "scheduler.sqlite3"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("QUEUE_BACKEND", "memory")
    monkeypatch.setenv("BACKEND", "slurm")
    monkeypatch.setattr(persistence, "_MEMORY_REDIS", persistence._MemoryRedis())
    persistence.bootstrap_storage()


def test_sqlite_enqueue_and_state_flow(monkeypatch, tmp_path):
    _configure_sqlite(monkeypatch, tmp_path)

    spec = JobSpec(job_id="job-1", image="", cmd=["echo", "hello"], gpus=1)
    status, created = persistence.enqueue_job(spec)

    assert created is True
    assert status.state == JobState.QUEUED
    assert persistence.redis_client().llen("jobs:queue") == 1
    assert persistence.get_job_spec("job-1") is not None

    persistence.place_job("job-1", "node-a")
    persistence.store_backend_ref("job-1", "12345")
    assert persistence.get_backend_ref("job-1") == "12345"
    persistence.set_job_state("job-1", "RUNNING")
    persistence.set_job_state("job-1", "DONE", exit_code=0)

    final = persistence.get_job_status("job-1")
    assert final is not None
    assert final.state == JobState.DONE
    assert final.exit_code == 0

    jobs = persistence.list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["backend_ref"] == "12345"
    assert jobs[0]["state"] == "DONE"


def test_sqlite_policy_and_nodes(monkeypatch, tmp_path):
    _configure_sqlite(monkeypatch, tmp_path)

    default_policy = persistence.get_active_policy()
    assert default_policy == SchedulerPolicy.FIFO

    updated = persistence.set_active_policy("ROUND_ROBIN", updated_by="test")
    assert updated == SchedulerPolicy.ROUND_ROBIN
    assert persistence.get_active_policy() == SchedulerPolicy.ROUND_ROBIN

    persistence.upsert_node(NodeInfo(node_id="gpu-01", labels={"partition": "gpu"}, gpus=[]))
    nodes = persistence.list_nodes()
    assert len(nodes) == 1
    assert nodes[0].node_id == "gpu-01"
    assert nodes[0].labels.get("partition") == "gpu"


def test_sqlite_metrics_summary(monkeypatch, tmp_path):
    _configure_sqlite(monkeypatch, tmp_path)

    spec = JobSpec(job_id="job-1", image="", cmd=["echo", "hello"], gpus=1)
    persistence.enqueue_job(spec)
    persistence.redis_client().lpop("jobs:queue")
    persistence.place_job("job-1", "node-a")
    persistence.set_job_state("job-1", "RUNNING")
    persistence.set_job_state("job-1", "DONE", exit_code=0)
    persistence.upsert_node(NodeInfo(node_id="gpu-01", labels={}, gpus=[]))

    summary = persistence.read_metrics_summary(window_minutes=60, fresh_node_seconds=120)

    assert summary["queue_depth"] == 0
    assert summary["jobs"]["done"] == 1
    assert summary["nodes"]["total"] == 1
    assert summary["windowed_terminal_counts"]["done"] == 1
