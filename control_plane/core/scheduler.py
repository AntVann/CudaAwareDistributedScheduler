import logging
from dataclasses import dataclass
from typing import List

from control_plane.core.models import JobSpec, SchedulerPolicy
from control_plane.core.persistence import (
    get_active_policy,
    get_job_spec,
    pg_conn,
    place_job,
    redis_client,
)

logger = logging.getLogger("control_plane.scheduler")

_ASSIGN_Q_PREFIX = "assign:"
_QUEUE_KEY = "jobs:queue"
_ROUND_ROBIN_KEY = "rr:idx"


@dataclass(frozen=True)
class NodeCandidate:
    node_id: str
    gpu_count: int
    avg_utilization: float


class NaiveScheduler:
    """
    Background scheduler loop that keeps FIFO dequeue order but varies node selection
    according to the active scheduling policy.
    """

    def __init__(self, loop_secs: int = 1, recent_secs: int = 30):
        self.loop_secs = loop_secs
        self.recent_secs = recent_secs
        self.active_policy = SchedulerPolicy.FIFO

    def load_active_policy(self) -> SchedulerPolicy:
        self.active_policy = get_active_policy()
        return self.active_policy

    def set_active_policy(self, policy: SchedulerPolicy) -> None:
        self.active_policy = policy

    def tick(self):
        r = redis_client()
        job_id = r.lpop(_QUEUE_KEY)
        if not job_id:
            return

        spec = get_job_spec(job_id) or JobSpec(job_id=job_id, image="", cmd=[])
        nodes = self._recent_nodes(self.recent_secs)
        eligible_nodes = [node for node in nodes if node.gpu_count >= spec.gpus]
        if not eligible_nodes:
            r.lpush(_QUEUE_KEY, job_id)
            logger.info("No eligible nodes for job %s under policy %s", job_id, self.active_policy.value)
            return

        node_id = self._select_node(r, spec, eligible_nodes)
        r.rpush(f"{_ASSIGN_Q_PREFIX}{node_id}", job_id)
        place_job(job_id, node_id)
        logger.info("Placed job %s on node %s with policy %s", job_id, node_id, self.active_policy.value)

    def _select_node(self, r, spec: JobSpec, eligible_nodes: List[NodeCandidate]) -> str:
        ordered = sorted(eligible_nodes, key=lambda node: node.node_id)

        if self.active_policy == SchedulerPolicy.FIFO:
            return ordered[0].node_id

        if self.active_policy == SchedulerPolicy.ROUND_ROBIN:
            idx = (int(r.incr(_ROUND_ROBIN_KEY)) - 1) % len(ordered)
            return ordered[idx].node_id

        if self.active_policy == SchedulerPolicy.BINPACK:
            selected = min(
                ordered,
                key=lambda node: (
                    node.gpu_count - spec.gpus,
                    -node.avg_utilization,
                    node.node_id,
                ),
            )
            return selected.node_id

        logger.warning("Unknown scheduler policy %s; falling back to FIFO", self.active_policy.value)
        return ordered[0].node_id

    def _recent_nodes(self, seconds: int) -> List[NodeCandidate]:
        with pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT node_id, gpus
                    FROM nodes
                    WHERE last_seen > NOW() - INTERVAL %s
                    ORDER BY node_id
                    """,
                    (f"{seconds} seconds",),
                )
                rows = cur.fetchall()

        candidates: List[NodeCandidate] = []
        for node_id, gpus in rows:
            gpu_entries = gpus or []
            if isinstance(gpu_entries, str):
                gpu_entries = []
            utilization_values = []
            for gpu in gpu_entries:
                if isinstance(gpu, dict):
                    utilization_values.append(float(gpu.get("utilization", 0.0)))
            avg_utilization = sum(utilization_values) / len(utilization_values) if utilization_values else 0.0
            candidates.append(
                NodeCandidate(
                    node_id=node_id,
                    gpu_count=len(gpu_entries),
                    avg_utilization=avg_utilization,
                )
            )
        return candidates
