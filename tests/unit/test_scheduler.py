from control_plane.core import scheduler as scheduler_module
from control_plane.core.models import JobSpec, SchedulerPolicy
from control_plane.core.scheduler import NodeCandidate


class FakeRedis:
    def __init__(self, job_ids=None):
        self.job_ids = list(job_ids or ["job-1"])
        self.left_pushes = []
        self.right_pushes = []
        self.counters = {}

    def lpop(self, key):
        assert key == "jobs:queue"
        if not self.job_ids:
            return None
        return self.job_ids.pop(0)

    def lpush(self, key, value):
        self.left_pushes.append((key, value))

    def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def rpush(self, key, value):
        self.right_pushes.append((key, value))


class SchedulerStub(scheduler_module.NaiveScheduler):
    def __init__(self, nodes):
        super().__init__()
        self.nodes = nodes

    def _recent_nodes(self, seconds):
        assert seconds == self.recent_secs
        return self.nodes


def test_tick_requeues_job_when_no_eligible_nodes(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(scheduler_module, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(
        scheduler_module,
        "get_job_spec",
        lambda job_id: JobSpec(job_id=job_id, image="", cmd=["echo", "hi"], gpus=2),
    )
    place_calls = []
    monkeypatch.setattr(scheduler_module, "place_job", lambda job_id, node_id: place_calls.append((job_id, node_id)))

    SchedulerStub(nodes=[NodeCandidate(node_id="node-a", gpu_count=1, avg_utilization=0.0)]).tick()

    assert fake_redis.left_pushes == [("jobs:queue", "job-1")]
    assert fake_redis.right_pushes == []
    assert place_calls == []


def test_fifo_selects_first_eligible_node_in_sorted_order(monkeypatch):
    fake_redis = FakeRedis()
    scheduler = SchedulerStub(
        nodes=[
            NodeCandidate(node_id="node-b", gpu_count=4, avg_utilization=10.0),
            NodeCandidate(node_id="node-a", gpu_count=3, avg_utilization=20.0),
            NodeCandidate(node_id="node-c", gpu_count=1, avg_utilization=30.0),
        ]
    )
    scheduler.set_active_policy(SchedulerPolicy.FIFO)

    monkeypatch.setattr(scheduler_module, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(
        scheduler_module,
        "get_job_spec",
        lambda job_id: JobSpec(job_id=job_id, image="", cmd=["echo", "hi"], gpus=2),
    )
    place_calls = []
    monkeypatch.setattr(scheduler_module, "place_job", lambda job_id, node_id: place_calls.append((job_id, node_id)))

    scheduler.tick()

    assert fake_redis.right_pushes == [("assign:node-a", "job-1")]
    assert place_calls == [("job-1", "node-a")]


def test_round_robin_cycles_across_eligible_nodes():
    scheduler = scheduler_module.NaiveScheduler()
    scheduler.set_active_policy(SchedulerPolicy.ROUND_ROBIN)
    fake_redis = FakeRedis(job_ids=[])
    spec = JobSpec(job_id="job-1", image="", cmd=["echo"], gpus=1)
    nodes = [
        NodeCandidate(node_id="node-b", gpu_count=2, avg_utilization=0.0),
        NodeCandidate(node_id="node-a", gpu_count=2, avg_utilization=0.0),
    ]

    first = scheduler._select_node(fake_redis, spec, nodes)
    second = scheduler._select_node(fake_redis, spec, nodes)
    third = scheduler._select_node(fake_redis, spec, nodes)

    assert (first, second, third) == ("node-a", "node-b", "node-a")


def test_binpack_uses_surplus_then_utilization_then_node_id():
    scheduler = scheduler_module.NaiveScheduler()
    scheduler.set_active_policy(SchedulerPolicy.BINPACK)
    fake_redis = FakeRedis(job_ids=[])
    spec = JobSpec(job_id="job-1", image="", cmd=["echo"], gpus=2)
    nodes = [
        NodeCandidate(node_id="node-z", gpu_count=4, avg_utilization=95.0),
        NodeCandidate(node_id="node-c", gpu_count=3, avg_utilization=40.0),
        NodeCandidate(node_id="node-b", gpu_count=3, avg_utilization=40.0),
    ]

    selected = scheduler._select_node(fake_redis, spec, nodes)

    assert selected == "node-b"
