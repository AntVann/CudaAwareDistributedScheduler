from control_plane.core import scheduler as scheduler_module
from control_plane.core.models import JobSpec, JobState, JobStatus, SchedulerPolicy
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
    monkeypatch.setattr(scheduler_module, "get_job_status", lambda job_id: None)
    monkeypatch.setattr(
        scheduler_module,
        "get_job_spec",
        lambda job_id: JobSpec(job_id=job_id, project="default", image="", cmd=["echo", "hi"], gpus=2),
    )
    place_calls = []
    monkeypatch.setattr(scheduler_module, "place_job", lambda job_id, node_id, decision=None: place_calls.append((job_id, node_id)))

    SchedulerStub(nodes=[NodeCandidate(node_id="node-a", gpu_count=1, avg_utilization=0.0)]).tick()

    assert fake_redis.left_pushes == []
    assert fake_redis.right_pushes == [("jobs:queue", "job-1")]
    assert place_calls == []


def test_tick_persists_no_placement_decision_when_no_eligible_nodes(monkeypatch):
    """When tick() pops a job and finds no eligible nodes, it should persist a
    structured placement_decision so the UI can show *why* the job is stuck."""
    fake_redis = FakeRedis()
    decisions = []

    monkeypatch.setattr(scheduler_module, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(scheduler_module, "get_job_status", lambda job_id: None)
    monkeypatch.setattr(
        scheduler_module,
        "get_job_spec",
        lambda job_id: JobSpec(job_id=job_id, project="default", image="", cmd=["echo"], gpus=4),
    )
    monkeypatch.setattr(
        scheduler_module,
        "set_placement_decision",
        lambda job_id, decision: decisions.append((job_id, decision)),
    )
    monkeypatch.setattr(scheduler_module, "place_job", lambda *args, **kwargs: None)

    # Both nodes are too small for the requested 4 GPUs.
    SchedulerStub(
        nodes=[
            NodeCandidate(node_id="node-a", gpu_count=2, avg_utilization=0.0),
            NodeCandidate(node_id="node-b", gpu_count=2, avg_utilization=0.0),
        ]
    ).tick()

    assert fake_redis.right_pushes == [("jobs:queue", "job-1")]
    assert len(decisions) == 1
    job_id, decision = decisions[0]
    assert job_id == "job-1"
    assert decision["chosen_node_id"] is None
    assert "no eligible nodes" in decision["chosen_reason"]
    assert len(decision["candidates"]) == 2
    for cand in decision["candidates"]:
        assert cand["eligible"] is False
        assert cand["selected"] is False
        assert "rejected_reason" in cand
        assert "not enough GPUs" in cand["rejected_reason"]


def test_tick_skips_jobs_already_cancelled_before_dispatch(monkeypatch):
    """If a job was cancelled while sitting in the queue, the scheduler must
    not dispatch it to the backend on its next tick."""
    fake_redis = FakeRedis()
    submit_calls = []
    place_calls = []

    class TrackingBackend:
        def submit(self, spec, node_hint=None):
            submit_calls.append((spec.job_id, node_hint))
            return "ignored"

    scheduler = SchedulerStub(nodes=[NodeCandidate(node_id="node-a", gpu_count=2, avg_utilization=0.0)])
    scheduler.backend = TrackingBackend()

    cancelled_status = JobStatus(state=JobState.CANCELLED, reason="cancelled by operator")

    monkeypatch.setattr(scheduler_module, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(scheduler_module, "get_job_status", lambda job_id: cancelled_status)
    monkeypatch.setattr(
        scheduler_module,
        "get_job_spec",
        lambda job_id: JobSpec(job_id=job_id, project="default", image="", cmd=["echo"], gpus=1),
    )
    monkeypatch.setattr(scheduler_module, "place_job", lambda job_id, node_id, decision=None: place_calls.append((job_id, node_id)))

    scheduler.tick()

    # Job is dropped silently — no backend dispatch, no place_job, no requeue.
    assert submit_calls == []
    assert place_calls == []
    assert fake_redis.right_pushes == []


def test_tick_marks_job_failed_when_backend_submit_raises(monkeypatch):
    fake_redis = FakeRedis()
    submit_calls = []
    failed_calls = []
    place_calls = []

    class ExplodingBackend:
        def submit(self, spec, node_hint=None):
            submit_calls.append((spec.job_id, node_hint))
            raise RuntimeError("sbatch failed")

    scheduler = SchedulerStub(nodes=[NodeCandidate(node_id="node-a", gpu_count=2, avg_utilization=0.0)])
    scheduler.backend = ExplodingBackend()

    monkeypatch.setattr(scheduler_module, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(scheduler_module, "get_job_status", lambda job_id: None)
    monkeypatch.setattr(
        scheduler_module,
        "get_job_spec",
        lambda job_id: JobSpec(job_id=job_id, project="default", image="", cmd=["echo", "hi"], gpus=1),
    )
    monkeypatch.setattr(
        scheduler_module,
        "set_job_state",
        lambda job_id, state, reason=None, exit_code=None: failed_calls.append((job_id, state, reason, exit_code)),
    )
    monkeypatch.setattr(scheduler_module, "place_job", lambda job_id, node_id, decision=None: place_calls.append((job_id, node_id)))

    scheduler.tick()

    assert submit_calls == [("job-1", "node-a")]
    assert failed_calls == [("job-1", "FAILED", "sbatch failed", None)]
    assert place_calls == []
    assert fake_redis.right_pushes == []


def test_tick_cancels_backend_job_when_place_job_raises(monkeypatch):
    fake_redis = FakeRedis()
    submit_calls = []
    cancel_calls = []
    failed_calls = []

    class Backend:
        def submit(self, spec, node_hint=None):
            submit_calls.append((spec.job_id, node_hint))

        def cancel(self, job_id):
            cancel_calls.append(job_id)
            return True

    scheduler = SchedulerStub(nodes=[NodeCandidate(node_id="node-a", gpu_count=2, avg_utilization=0.0)])
    scheduler.backend = Backend()

    monkeypatch.setattr(scheduler_module, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(scheduler_module, "get_job_status", lambda job_id: None)
    monkeypatch.setattr(
        scheduler_module,
        "get_job_spec",
        lambda job_id: JobSpec(job_id=job_id, project="default", image="", cmd=["echo", "hi"], gpus=1),
    )
    monkeypatch.setattr(
        scheduler_module,
        "place_job",
        lambda job_id, node_id, decision=None: (_ for _ in ()).throw(RuntimeError("db write failed")),
    )
    monkeypatch.setattr(
        scheduler_module,
        "set_job_state",
        lambda job_id, state, reason=None, exit_code=None: failed_calls.append((job_id, state, reason, exit_code)),
    )

    scheduler.tick()

    assert submit_calls == [("job-1", "node-a")]
    assert cancel_calls == ["job-1"]
    assert failed_calls == [("job-1", "FAILED", "db write failed", None)]


def test_tick_filters_nodes_by_partition_and_state(monkeypatch):
    fake_redis = FakeRedis()
    scheduler = SchedulerStub(
        nodes=[
            NodeCandidate(
                node_id="node-a",
                gpu_count=4,
                avg_utilization=0.0,
                partitions=("gpu-v100",),
                state="idle",
            ),
            NodeCandidate(
                node_id="node-b",
                gpu_count=4,
                avg_utilization=0.0,
                partitions=("gpu-a100",),
                state="drain",
            ),
            NodeCandidate(
                node_id="node-c",
                gpu_count=4,
                avg_utilization=0.0,
                partitions=("gpu-a100",),
                state="mixed",
            ),
        ]
    )
    scheduler.set_active_policy(SchedulerPolicy.FIFO)

    monkeypatch.setattr(scheduler_module, "redis_client", lambda: fake_redis)
    monkeypatch.setattr(scheduler_module, "get_job_status", lambda job_id: None)
    monkeypatch.setattr(
        scheduler_module,
        "get_job_spec",
        lambda job_id: JobSpec(
            job_id=job_id,
            project="default",
            image="",
            cmd=["echo", "hi"],
            gpus=1,
            metadata={"partition": "gpu-a100"},
        ),
    )
    place_calls = []
    monkeypatch.setattr(scheduler_module, "place_job", lambda job_id, node_id, decision=None: place_calls.append((job_id, node_id)))

    scheduler.tick()

    assert fake_redis.right_pushes == [("assign:node-c", "job-1")]
    assert place_calls == [("job-1", "node-c")]


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
    monkeypatch.setattr(scheduler_module, "get_job_status", lambda job_id: None)
    monkeypatch.setattr(
        scheduler_module,
        "get_job_spec",
        lambda job_id: JobSpec(job_id=job_id, project="default", image="", cmd=["echo", "hi"], gpus=2),
    )
    place_calls = []
    monkeypatch.setattr(scheduler_module, "place_job", lambda job_id, node_id, decision=None: place_calls.append((job_id, node_id)))

    scheduler.tick()

    assert fake_redis.right_pushes == [("assign:node-a", "job-1")]
    assert place_calls == [("job-1", "node-a")]


def test_round_robin_cycles_across_eligible_nodes():
    scheduler = scheduler_module.NaiveScheduler()
    scheduler.set_active_policy(SchedulerPolicy.ROUND_ROBIN)
    fake_redis = FakeRedis(job_ids=[])
    spec = JobSpec(job_id="job-1", project="default", image="", cmd=["echo"], gpus=1)
    nodes = [
        NodeCandidate(node_id="node-b", gpu_count=2, avg_utilization=0.0),
        NodeCandidate(node_id="node-a", gpu_count=2, avg_utilization=0.0),
    ]

    first, _ = scheduler._select_node(fake_redis, spec, nodes, nodes)
    second, _ = scheduler._select_node(fake_redis, spec, nodes, nodes)
    third, _ = scheduler._select_node(fake_redis, spec, nodes, nodes)

    assert (first, second, third) == ("node-a", "node-b", "node-a")


def test_binpack_uses_surplus_then_utilization_then_node_id():
    scheduler = scheduler_module.NaiveScheduler()
    scheduler.set_active_policy(SchedulerPolicy.BINPACK)
    fake_redis = FakeRedis(job_ids=[])
    spec = JobSpec(job_id="job-1", project="default", image="", cmd=["echo"], gpus=2)
    nodes = [
        NodeCandidate(node_id="node-z", gpu_count=4, avg_utilization=95.0),
        NodeCandidate(node_id="node-c", gpu_count=3, avg_utilization=40.0),
        NodeCandidate(node_id="node-b", gpu_count=3, avg_utilization=40.0),
    ]

    selected, _ = scheduler._select_node(fake_redis, spec, nodes, nodes)

    assert selected == "node-b"


def test_default_partition_from_backend_filters_candidates():
    scheduler = scheduler_module.NaiveScheduler()
    scheduler.backend = type("Backend", (), {"default_partition": "gpu-a100"})()
    spec = JobSpec(job_id="job-1", project="default", image="", cmd=["echo"], gpus=1)
    nodes = [
        NodeCandidate(node_id="node-a", gpu_count=2, avg_utilization=0.0, partitions=("gpu-v100",), state="idle"),
        NodeCandidate(node_id="node-b", gpu_count=2, avg_utilization=0.0, partitions=("gpu-a100",), state="idle"),
    ]

    eligible = scheduler._eligible_nodes(spec, nodes)

    assert [node.node_id for node in eligible] == ["node-b"]


def test_eligible_nodes_use_allocatable_gpu_count_when_provided():
    scheduler = scheduler_module.NaiveScheduler()
    spec = JobSpec(job_id="job-1", project="default", image="", cmd=["echo"], gpus=2)
    nodes = [
        NodeCandidate(
            node_id="node-a",
            gpu_count=4,
            available_gpu_count=1,
            avg_utilization=0.0,
            partitions=(),
            state="idle",
        ),
        NodeCandidate(
            node_id="node-b",
            gpu_count=4,
            available_gpu_count=2,
            avg_utilization=0.0,
            partitions=(),
            state="idle",
        ),
    ]

    eligible = scheduler._eligible_nodes(spec, nodes)

    assert [node.node_id for node in eligible] == ["node-b"]


def test_structured_state_string_with_drain_flag_is_not_schedulable():
    node = NodeCandidate(
        node_id="node-a",
        gpu_count=4,
        avg_utilization=0.0,
        partitions=("gpu",),
        state="base=idle,flags=['DRAIN']",
    )

    assert scheduler_module.NaiveScheduler._is_node_schedulable(node) is False
