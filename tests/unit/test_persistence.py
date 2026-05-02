import json

import pytest

from control_plane.core import persistence
from control_plane.core.models import JobSpec


class FakeRedis:
    def __init__(self):
        self.rpush_calls = []
        self.set_calls = []

    def rpush(self, key, value):
        self.rpush_calls.append((key, value))

    def set(self, key, value):
        self.set_calls.append((key, value))


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self._fetchone = None
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params):
        normalized = " ".join(sql.split())

        if normalized.startswith("INSERT INTO jobs (job_id, project, submitted_by, spec, status, timestamps)"):
            job_id, project, _submitted_by, _spec_json, status, timestamps_json = params
            if job_id in self.db:
                self._fetchone = None
                self.rowcount = 0
                return

            row = (status, None, [], json.loads(timestamps_json), None, None, project)
            self.db[job_id] = row
            self._fetchone = row
            self.rowcount = 1
            return

        if normalized.startswith("SELECT status, node_id, gpu_ids, timestamps, exit_code, reason, project FROM jobs"):
            job_id = params[0]
            self._fetchone = self.db.get(job_id)
            self.rowcount = 1 if self._fetchone else 0
            return

        if normalized.startswith("UPDATE jobs SET status=%s, exit_code=%s, reason=%s, timestamps"):
            # New signature: (state, exit_code, reason, extras_json, state_keyed_json, job_id)
            # The Postgres SQL is `extras::jsonb || existing || state_keyed::jsonb`
            # so extras only fill in missing keys, existing keeps its values, and
            # the state-keyed timestamp is always set last (winning on conflict).
            state, exit_code, reason, extras_json, state_keyed_json, job_id = params
            existing = self.db.get(job_id)
            if existing is None:
                self.rowcount = 0
                return

            _old_state, node_id, gpu_ids, timestamps, _old_exit_code, _old_reason, project = existing
            extras = json.loads(extras_json or "{}")
            state_keyed = json.loads(state_keyed_json or "{}")
            merged_timestamps = dict(extras)
            merged_timestamps.update(timestamps or {})
            merged_timestamps.update(state_keyed)
            self.db[job_id] = (state, node_id, gpu_ids, merged_timestamps, exit_code, reason, project)
            self.rowcount = 1
            return

        raise AssertionError(f"Unexpected SQL: {normalized}")

    def fetchone(self):
        return self._fetchone


class FakeConnection:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return FakeCursor(self.db)


def test_enqueue_job_creates_new_queue_entry(monkeypatch):
    db = {}
    fake_redis = FakeRedis()
    monkeypatch.setattr(persistence, "pg_conn", lambda: FakeConnection(db))
    monkeypatch.setattr(persistence, "redis_client", lambda: fake_redis)

    status, created = persistence.enqueue_job(
        JobSpec(job_id="job-1", project="vision", image="", cmd=["echo", "hi"]),
        submitted_by="alice",
    )

    assert created is True
    assert status.state.value == "QUEUED"
    assert status.project == "vision"
    assert fake_redis.rpush_calls == [("jobs:queue", "job-1")]
    assert len(fake_redis.set_calls) == 1


def test_enqueue_job_duplicate_is_idempotent(monkeypatch):
    db = {
        "job-1": ("DONE", "node-a", [], {"enqueued": 1.0, "done": 2.0}, 0, None, "vision"),
    }
    fake_redis = FakeRedis()
    monkeypatch.setattr(persistence, "pg_conn", lambda: FakeConnection(db))
    monkeypatch.setattr(persistence, "redis_client", lambda: fake_redis)

    status, created = persistence.enqueue_job(JobSpec(job_id="job-1", project="vision", image="", cmd=["echo", "changed"]))

    assert created is False
    assert status.state.value == "DONE"
    assert status.node_id == "node-a"
    assert status.project == "vision"
    assert fake_redis.rpush_calls == []
    assert fake_redis.set_calls == []


def test_set_job_state_updates_reason_and_timestamp(monkeypatch):
    db = {
        "job-1": ("RUNNING", "node-a", [], {"running": 1.0}, None, None, "vision"),
    }
    monkeypatch.setattr(persistence, "pg_conn", lambda: FakeConnection(db))

    persistence.set_job_state("job-1", "FAILED", exit_code=127, reason="apptainer missing")

    state, _node_id, _gpu_ids, timestamps, exit_code, reason, project = db["job-1"]
    assert state == "FAILED"
    assert exit_code == 127
    assert reason == "apptainer missing"
    assert project == "vision"
    assert "failed" in timestamps


def test_set_job_state_raises_for_missing_job(monkeypatch):
    monkeypatch.setattr(persistence, "pg_conn", lambda: FakeConnection({}))

    with pytest.raises(KeyError):
        persistence.set_job_state("missing-job", "FAILED", exit_code=1)
