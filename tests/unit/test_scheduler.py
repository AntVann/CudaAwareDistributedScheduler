import json

from control_plane.core import scheduler as scheduler_module


class FakeRedis:
    def __init__(self, job_id="job-1"):
        self.job_id = job_id
        self.left_pushes = []
        self.right_pushes = []
        self.counters = {}

    def lpop(self, key):
        assert key == "jobs:queue"
        job_id, self.job_id = self.job_id, None
        return job_id

    def lpush(self, key, value):
        self.left_pushes.append((key, value))

    def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def rpush(self, key, value):
        self.right_pushes.append((key, value))


class FakeCursor:
    def __init__(self):
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params):
        self.executed.append((" ".join(sql.split()), params))


class FakeConnection:
    def __init__(self):
        self.cursor_obj = FakeCursor()
        self.commit_called = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commit_called = True


class SchedulerStub(scheduler_module.NaiveScheduler):
    def __init__(self, nodes):
        super().__init__()
        self.nodes = nodes

    def _recent_nodes(self, seconds):
        assert seconds == self.recent_secs
        return self.nodes


def test_tick_requeues_job_when_no_nodes(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(scheduler_module, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(scheduler_module, "pg_conn", lambda: FakeConnection())

    SchedulerStub(nodes=[]).tick()

    assert fake_redis.left_pushes == [("jobs:queue", "job-1")]
    assert fake_redis.right_pushes == []


def test_tick_places_job_and_records_placed_timestamp(monkeypatch):
    fake_redis = FakeRedis()
    fake_conn = FakeConnection()
    monkeypatch.setattr(scheduler_module, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(scheduler_module, "pg_conn", lambda: fake_conn)

    SchedulerStub(nodes=["node-a", "node-b"]).tick()

    assert fake_redis.right_pushes == [("assign:node-a", "job-1")]
    assert fake_conn.commit_called is True

    sql, params = fake_conn.cursor_obj.executed[0]
    assert "UPDATE jobs" in sql
    assert params[0] == "PLACED"
    assert params[1] == "node-a"
    assert json.loads(params[2])["placed"] > 0
    assert params[3] == "job-1"
