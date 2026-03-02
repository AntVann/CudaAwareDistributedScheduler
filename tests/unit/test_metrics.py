from control_plane.core import persistence


class FakeRedis:
    def __init__(self, queue_depth):
        self.queue_depth = queue_depth

    def llen(self, key):
        assert key == "jobs:queue"
        return self.queue_depth


class FakeCursor:
    def __init__(self, node_row, job_rows):
        self.node_row = node_row
        self.job_rows = job_rows
        self.fetchone_result = None
        self.fetchall_result = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        if normalized.startswith("SELECT COUNT(*) AS total, COUNT(*) FILTER"):
            self.fetchone_result = self.node_row
            return
        if normalized.startswith("SELECT status, timestamps FROM jobs WHERE timestamps IS NOT NULL"):
            self.fetchall_result = self.job_rows
            return
        raise AssertionError(f"Unexpected SQL: {normalized}")

    def fetchone(self):
        return self.fetchone_result

    def fetchall(self):
        return self.fetchall_result


class FakeConnection:
    def __init__(self, node_row, job_rows):
        self.node_row = node_row
        self.job_rows = job_rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return FakeCursor(self.node_row, self.job_rows)


def test_metrics_summary_handles_empty_state(monkeypatch):
    monkeypatch.setattr(persistence, "redis_client", lambda: FakeRedis(queue_depth=0))
    monkeypatch.setattr(
        persistence,
        "pg_conn",
        lambda: FakeConnection({"total": 0, "fresh": 0}, []),
    )
    monkeypatch.setattr(
        persistence,
        "job_summary",
        lambda: {
            "queued": 0,
            "placed": 0,
            "running": 0,
            "done": 0,
            "failed": 0,
            "cancelled": 0,
        },
    )

    summary = persistence.read_metrics_summary(window_minutes=60, fresh_node_seconds=30)

    assert summary["queue_depth"] == 0
    assert summary["jobs"]["queued"] == 0
    assert summary["nodes"] == {"total": 0, "fresh": 0, "stale": 0}
    assert summary["latency_ms"] == {
        "placement_p50": 0,
        "placement_p95": 0,
        "run_p50": 0,
        "run_p95": 0,
    }
    assert summary["windowed_terminal_counts"] == {"done": 0, "failed": 0}
    assert summary["window_minutes"] == 60


def test_metrics_latency_ignores_incomplete_timestamp_sets(monkeypatch):
    now = 1_000.0
    monkeypatch.setattr(persistence.time, "time", lambda: now)
    monkeypatch.setattr(persistence, "redis_client", lambda: FakeRedis(queue_depth=3))
    monkeypatch.setattr(
        persistence,
        "pg_conn",
        lambda: FakeConnection(
            {"total": 2, "fresh": 1},
            [
                {
                    "status": "DONE",
                    "timestamps": {
                        "enqueued": 600.0,
                        "placed": 700.0,
                        "running": 750.0,
                        "done": 900.0,
                    },
                },
                {
                    "status": "FAILED",
                    "timestamps": {
                        "enqueued": 610.0,
                        "placed": 640.0,
                        "failed": 800.0,
                    },
                },
                {
                    "status": "DONE",
                    "timestamps": {
                        "enqueued": 620.0,
                        "running": 700.0,
                    },
                },
            ],
        ),
    )
    monkeypatch.setattr(
        persistence,
        "job_summary",
        lambda: {
            "queued": 1,
            "placed": 0,
            "running": 1,
            "done": 1,
            "failed": 1,
            "cancelled": 0,
        },
    )

    summary = persistence.read_metrics_summary(window_minutes=10, fresh_node_seconds=30)

    assert summary["queue_depth"] == 3
    assert summary["nodes"] == {"total": 2, "fresh": 1, "stale": 1}
    assert summary["latency_ms"]["placement_p50"] == 65_000
    assert summary["latency_ms"]["placement_p95"] == 96_500
    assert summary["latency_ms"]["run_p50"] == 150_000
    assert summary["latency_ms"]["run_p95"] == 150_000
    assert summary["windowed_terminal_counts"] == {"done": 1, "failed": 1}
