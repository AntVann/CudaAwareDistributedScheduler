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

    spec = JobSpec(job_id="job-1", project="default", image="", cmd=["echo", "hello"], gpus=1)
    status, created = persistence.enqueue_job(spec)

    assert created is True
    assert status.state == JobState.QUEUED
    assert status.project == "default"
    assert persistence.redis_client().llen("jobs:queue") == 1
    assert persistence.get_job_spec("job-1") is not None
    assert persistence.get_job_project("job-1") == "default"

    persistence.place_job("job-1", "node-a")
    persistence.store_backend_ref("job-1", "12345")
    assert persistence.get_backend_ref("job-1") == "12345"
    persistence.set_job_state("job-1", "RUNNING")
    persistence.set_job_state("job-1", "DONE", exit_code=0)

    final = persistence.get_job_status("job-1")
    assert final is not None
    assert final.state == JobState.DONE
    assert final.exit_code == 0
    assert final.project == "default"

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

    spec = JobSpec(job_id="job-1", project="default", image="", cmd=["echo", "hello"], gpus=1)
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


def test_sqlite_set_job_state_merges_caller_supplied_timestamps(monkeypatch, tmp_path):
    """Regression for #6: when the SLURM poller jumps PLACED -> DONE in one tick
    (because the job was very short), it passes timestamps={running: sacct_start,
    done: sacct_end}. We must preserve `running` so the run-latency calc has
    something to measure against."""
    _configure_sqlite(monkeypatch, tmp_path)

    spec = JobSpec(job_id="fast-job", project="default", image="", cmd=["true"], gpus=1)
    persistence.enqueue_job(spec)
    persistence.place_job("fast-job", "node-a")

    # Simulate the poller catching a state transition from PLACED to DONE,
    # with sacct having reported both Start (running) and End (done).
    sacct_start = 1_000_000.0
    sacct_end = 1_000_005.5
    persistence.set_job_state(
        "fast-job",
        "DONE",
        exit_code=0,
        timestamps={"running": sacct_start, "done": sacct_end},
    )

    status = persistence.get_job_status("fast-job")
    assert status is not None
    assert status.state == JobState.DONE
    # `running` was filled in from extras (no prior value).
    assert status.timestamps.get("running") == sacct_start
    # `done` was the new state's key, so the caller-supplied value wins over
    # current wall clock.
    assert status.timestamps.get("done") == sacct_end


def test_sqlite_set_job_state_does_not_overwrite_existing_timestamps(monkeypatch, tmp_path):
    """Caller-supplied extras must not clobber timestamps already on disk."""
    _configure_sqlite(monkeypatch, tmp_path)

    spec = JobSpec(job_id="slow-job", project="default", image="", cmd=["sleep"], gpus=1)
    persistence.enqueue_job(spec)
    persistence.place_job("slow-job", "node-a")
    persistence.set_job_state("slow-job", "RUNNING")  # records running=now()

    status_before = persistence.get_job_status("slow-job")
    assert status_before is not None
    running_before = status_before.timestamps["running"]

    # Poller reports DONE later, with sacct's Start which differs from when
    # we recorded RUNNING. We should keep our own recorded `running`.
    persistence.set_job_state(
        "slow-job",
        "DONE",
        exit_code=0,
        timestamps={"running": 0.0, "done": 1_999_999.0},
    )
    status_after = persistence.get_job_status("slow-job")
    assert status_after is not None
    assert status_after.timestamps["running"] == running_before  # unchanged
    assert status_after.timestamps["done"] == 1_999_999.0


def test_sqlite_bootstrap_admin_token_and_resolve(monkeypatch, tmp_path):
    _configure_sqlite(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTH_MODE", "token")
    monkeypatch.setenv("ADMIN_API_TOKEN", "sqlite-admin-token")

    created = persistence.ensure_bootstrap_admin_token()

    assert created is True
    principal = persistence.resolve_human_token("sqlite-admin-token")
    assert principal is not None
    assert principal["subject"] == "bootstrap-admin"
    assert principal["role"] == "admin"
    assert principal["projects"] == ["*"]


def test_sqlite_resolve_human_token_rejects_expired_token(monkeypatch, tmp_path):
    _configure_sqlite(monkeypatch, tmp_path)

    with persistence._sqlite_conn() as conn:
        conn.execute(
            """
            INSERT INTO api_tokens
            (id, token_hash, subject, role, projects, active, expires_at, created_at, created_by)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (
                "tok-expired",
                persistence.hash_token("expired-token"),
                "alice",
                "user",
                '["default"]',
                "2000-01-01T00:00:00",
                "2000-01-01T00:00:00",
                "test",
            ),
        )

    assert persistence.resolve_human_token("expired-token") is None


def test_sqlite_token_request_full_flow(monkeypatch, tmp_path):
    _configure_sqlite(monkeypatch, tmp_path)

    created = persistence.create_token_request(
        subject_name="alice",
        email="alice@example.com",
        requested_projects=["vision", "nlp"],
        purpose="demo access",
    )
    assert created["status"] == "PENDING"

    pending = persistence.list_token_requests(status="PENDING")
    assert len(pending) == 1
    assert pending[0]["requested_projects"] == ["vision", "nlp"]

    delivered = {}

    def fake_deliver(email: str, subject_name: str, token: str) -> None:
        delivered["email"] = email
        delivered["subject_name"] = subject_name
        delivered["token"] = token

    approved = persistence.approve_token_request(
        created["request_id"],
        reviewed_by="admin-user",
        deliver=fake_deliver,
        review_notes="approved",
    )

    assert approved["status"] == "APPROVED"
    assert delivered["email"] == "alice@example.com"

    principal = persistence.resolve_human_token(delivered["token"])
    assert principal is not None
    assert principal["subject"] == "alice"
    assert principal["role"] == "user"
    assert principal["projects"] == ["vision", "nlp"]

    tokens = persistence.list_api_tokens()
    assert len(tokens) == 1
    assert tokens[0]["subject"] == "alice"
    assert tokens[0]["active"] is True


def test_sqlite_approve_token_request_rolls_back_on_delivery_failure(monkeypatch, tmp_path):
    _configure_sqlite(monkeypatch, tmp_path)

    created = persistence.create_token_request(
        subject_name="bob",
        email="bob@example.com",
        requested_projects=["default"],
        purpose="demo access",
    )

    def broken_deliver(email: str, subject_name: str, token: str) -> None:
        raise RuntimeError("smtp failed")

    try:
        persistence.approve_token_request(
            created["request_id"],
            reviewed_by="admin-user",
            deliver=broken_deliver,
        )
    except RuntimeError as exc:
        assert str(exc) == "smtp failed"
    else:
        raise AssertionError("approve_token_request should have raised")

    pending = persistence.list_token_requests(status="PENDING")
    assert len(pending) == 1
    assert pending[0]["request_id"] == created["request_id"]
    assert persistence.list_api_tokens() == []


def test_sqlite_approve_token_request_response_mode_returns_plaintext_token(monkeypatch, tmp_path):
    _configure_sqlite(monkeypatch, tmp_path)

    created = persistence.create_token_request(
        subject_name="erin",
        email="erin@example.com",
        requested_projects=["default"],
        purpose="hpc access",
    )
    delivered = {"called": False}

    def fake_deliver(email: str, subject_name: str, token: str) -> None:
        delivered["called"] = True

    approved = persistence.approve_token_request(
        created["request_id"],
        reviewed_by="admin-user",
        deliver=fake_deliver,
        delivery_mode="response",
    )

    assert approved["status"] == "APPROVED"
    assert approved["plaintext_token"]
    assert delivered["called"] is False

    principal = persistence.resolve_human_token(approved["plaintext_token"])
    assert principal is not None
    assert principal["subject"] == "erin"
    assert principal["projects"] == ["default"]


def test_sqlite_reject_and_revoke_token_request(monkeypatch, tmp_path):
    _configure_sqlite(monkeypatch, tmp_path)

    rejected = persistence.create_token_request(
        subject_name="carol",
        email="carol@example.com",
        requested_projects=["default"],
        purpose="should be rejected",
    )
    result = persistence.reject_token_request(rejected["request_id"], reviewed_by="admin-user", review_notes="no")
    assert result["status"] == "REJECTED"

    approved = persistence.create_token_request(
        subject_name="dave",
        email="dave@example.com",
        requested_projects=["default"],
        purpose="demo access",
    )
    delivered = {}

    def fake_deliver(email: str, subject_name: str, token: str) -> None:
        delivered["token"] = token

    token_result = persistence.approve_token_request(
        approved["request_id"],
        reviewed_by="admin-user",
        deliver=fake_deliver,
    )
    revoked = persistence.revoke_api_token(token_result["token_id"], revoked_by="admin-user")

    assert revoked == {"token_id": token_result["token_id"], "revoked": True}
    assert persistence.resolve_human_token(delivered["token"]) is None
