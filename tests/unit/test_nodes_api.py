from types import SimpleNamespace

from control_plane.api import nodes as nodes_api
from control_plane.core.models import NodeInfo


def test_nodes_endpoint_uses_live_backend_view_in_slurm_mode(monkeypatch):
    monkeypatch.setenv("BACKEND", "slurm")

    live_nodes = [NodeInfo(node_id="gpu-01", gpus=[], labels={"partition": "gpu"}, agent_health={}, last_seen=123.0)]
    backend = SimpleNamespace(list_nodes=lambda recent_secs: live_nodes)
    scheduler = SimpleNamespace(backend=backend, recent_secs=45)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(scheduler=scheduler)))

    persisted_calls = []
    monkeypatch.setattr(nodes_api, "persist_list_nodes", lambda: persisted_calls.append(True) or [])

    nodes = nodes_api.list_nodes(request)

    assert nodes == live_nodes
    assert persisted_calls == []


def test_nodes_endpoint_uses_persisted_nodes_outside_slurm(monkeypatch):
    monkeypatch.setenv("BACKEND", "redis-agent")
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(scheduler=None)))
    persisted_nodes = [NodeInfo(node_id="agent-01", gpus=[], labels={}, agent_health={}, last_seen=456.0)]
    monkeypatch.setattr(nodes_api, "persist_list_nodes", lambda: persisted_nodes)

    nodes = nodes_api.list_nodes(request)

    assert nodes == persisted_nodes
