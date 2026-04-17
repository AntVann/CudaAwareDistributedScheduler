from types import SimpleNamespace

from control_plane.api import metrics as metrics_api
from control_plane.core.models import NodeInfo


def test_metrics_endpoint_overrides_node_counts_with_live_slurm_view(monkeypatch):
    monkeypatch.setenv("BACKEND", "slurm")
    monkeypatch.setattr(
        metrics_api,
        "read_metrics_summary",
        lambda window_minutes, fresh_node_seconds: {
            "queue_depth": 0,
            "jobs": {
                "queued": 0,
                "placed": 0,
                "running": 0,
                "done": 0,
                "failed": 0,
                "cancelled": 0,
            },
            "nodes": {"total": 0, "fresh": 0, "stale": 0},
            "latency_ms": {
                "placement_p50": 0,
                "placement_p95": 0,
                "run_p50": 0,
                "run_p95": 0,
            },
            "windowed_terminal_counts": {"done": 0, "failed": 0},
            "window_minutes": window_minutes,
        },
    )
    monkeypatch.setattr(metrics_api.time, "time", lambda: 200.0)

    live_nodes = [
        NodeInfo(node_id="gpu-01", gpus=[], labels={}, agent_health={}, last_seen=195.0),
        NodeInfo(node_id="gpu-02", gpus=[], labels={}, agent_health={}, last_seen=150.0),
    ]
    backend = SimpleNamespace(list_nodes=lambda recent_secs: live_nodes)
    scheduler = SimpleNamespace(backend=backend, recent_secs=30)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(scheduler=scheduler)))

    summary = metrics_api.get_metrics_summary(request, window_minutes=15)

    assert summary["nodes"] == {"total": 2, "fresh": 1, "stale": 1}
    assert summary["window_minutes"] == 15
