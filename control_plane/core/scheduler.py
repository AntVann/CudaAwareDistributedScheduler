import logging
import time
from dataclasses import dataclass
from typing import List, Optional

from control_plane.core.backend import ExecutionBackend
from control_plane.core.models import JobSpec, SchedulerPolicy
from control_plane.core.persistence import (
    get_active_policy,
    get_job_spec,
    place_job,
    redis_client,
)

logger = logging.getLogger("control_plane.scheduler")

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

    def __init__(
        self,
        backend: Optional[ExecutionBackend] = None,
        loop_secs: int = 1,
        recent_secs: int = 30,
    ):
        self.backend = backend
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

        spec = get_job_spec(job_id) or JobSpec(job_id=job_id, project="default", image="", cmd=[])
        nodes = self._recent_nodes(self.recent_secs)
        eligible_nodes = [node for node in nodes if node.gpu_count >= spec.gpus]
        if not eligible_nodes:
            r.lpush(_QUEUE_KEY, job_id)
            logger.info("No eligible nodes for job %s under policy %s", job_id, self.active_policy.value)
            return

        node_id = self._select_node(r, spec, eligible_nodes)

        # Dispatch via backend if available, otherwise fall back to direct Redis push
        if self.backend:
            self.backend.submit(spec, node_hint=node_id)
        else:
            r.rpush(f"assign:{node_id}", job_id)

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
        # If we have a backend, use it for node discovery
        if self.backend:
            nodes = self.backend.list_nodes(recent_secs=seconds)
            candidates: List[NodeCandidate] = []
            for node in nodes:
                gpu_entries = node.gpus or []
                utilization_values = []
                for gpu in gpu_entries:
                    if isinstance(gpu, dict):
                        utilization_values.append(float(gpu.get("utilization", 0.0)))
                    elif hasattr(gpu, "utilization"):
                        utilization_values.append(float(gpu.utilization))
                avg_utilization = sum(utilization_values) / len(utilization_values) if utilization_values else 0.0
                candidates.append(
                    NodeCandidate(
                        node_id=node.node_id,
                        gpu_count=len(gpu_entries),
                        avg_utilization=avg_utilization,
                    )
                )
            return candidates

        # Fallback: persistence-based node listing for whichever DB backend is active.
        from control_plane.core.persistence import list_nodes as persist_list_nodes

        fresh_cutoff = time.time() - seconds
        candidates = []
        for node in persist_list_nodes():
            last_seen = node.last_seen or 0.0
            if float(last_seen) < fresh_cutoff:
                continue
            gpu_entries = node.gpus or []
            utilization_values = []
            for gpu in gpu_entries:
                if isinstance(gpu, dict):
                    utilization_values.append(float(gpu.get("utilization", 0.0)))
                elif hasattr(gpu, "utilization"):
                    utilization_values.append(float(gpu.utilization))
            avg_utilization = sum(utilization_values) / len(utilization_values) if utilization_values else 0.0
            candidates.append(
                NodeCandidate(
                    node_id=node.node_id,
                    gpu_count=len(gpu_entries),
                    avg_utilization=avg_utilization,
                )
            )
        return candidates
