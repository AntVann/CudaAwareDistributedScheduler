import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from control_plane.core.backend import ExecutionBackend
from control_plane.core.models import JobSpec, SchedulerPolicy
from control_plane.core.persistence import (
    get_active_policy,
    get_job_spec,
    place_job,
    redis_client,
    set_job_state,
)

logger = logging.getLogger("control_plane.scheduler")

_QUEUE_KEY = "jobs:queue"
_ROUND_ROBIN_KEY = "rr:idx"


@dataclass(frozen=True)
class NodeCandidate:
    node_id: str
    gpu_count: int
    avg_utilization: float
    partitions: tuple[str, ...] = ()
    state: str = ""
    available_gpu_count: Optional[int] = None

    @property
    def allocatable_gpu_count(self) -> int:
        if self.available_gpu_count is None:
            return self.gpu_count
        return max(self.available_gpu_count, 0)


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
        eligible_nodes = self._eligible_nodes(spec, nodes)
        if not eligible_nodes:
            r.rpush(_QUEUE_KEY, job_id)
            logger.info("No eligible nodes for job %s under policy %s", job_id, self.active_policy.value)
            return

        node_id, decision = self._select_node(r, spec, eligible_nodes, nodes)

        # Dispatch via backend if available, otherwise fall back to direct Redis push
        dispatched = False
        try:
            if self.backend:
                self.backend.submit(spec, node_hint=node_id)
            else:
                r.rpush(f"assign:{node_id}", job_id)
            dispatched = True
            place_job(job_id, node_id, decision=decision)
        except Exception as exc:
            logger.exception("Dispatch failed for job %s on node %s", job_id, node_id)
            if dispatched and self.backend:
                try:
                    if not self.backend.cancel(job_id):
                        logger.warning("Rollback cancel was not accepted for job %s after placement failure", job_id)
                except Exception:
                    logger.exception("Rollback cancel failed for job %s after placement failure", job_id)
            try:
                set_job_state(job_id, "FAILED", reason=str(exc) or exc.__class__.__name__)
            except Exception:
                logger.exception("Failed to mark job %s as FAILED after dispatch error", job_id)
            return

        logger.info("Placed job %s on node %s with policy %s", job_id, node_id, self.active_policy.value)

    def _select_node(
        self,
        r,
        spec: JobSpec,
        eligible_nodes: List[NodeCandidate],
        all_nodes: List[NodeCandidate],
    ) -> Tuple[str, Dict[str, Any]]:
        ordered = sorted(eligible_nodes, key=lambda node: node.node_id)
        policy = self.active_policy
        partition = self._desired_partition(spec)

        chosen: NodeCandidate
        rr_pointer: Optional[int] = None
        chosen_reason = ""

        if policy == SchedulerPolicy.FIFO:
            chosen = ordered[0]
            chosen_reason = "first eligible by node_id (FIFO)"
        elif policy == SchedulerPolicy.ROUND_ROBIN:
            counter = int(r.incr(_ROUND_ROBIN_KEY))
            idx = (counter - 1) % len(ordered)
            rr_pointer = counter
            chosen = ordered[idx]
            chosen_reason = f"round-robin pointer={counter} → index {idx} of {len(ordered)}"
        elif policy == SchedulerPolicy.BINPACK:
            chosen = min(
                ordered,
                key=lambda node: (
                    node.allocatable_gpu_count - spec.gpus,
                    -node.avg_utilization,
                    node.node_id,
                ),
            )
            slack = chosen.allocatable_gpu_count - spec.gpus
            chosen_reason = (
                f"tightest fit: slack={slack} GPU "
                f"(util={chosen.avg_utilization:.2f})"
            )
        else:
            logger.warning("Unknown scheduler policy %s; falling back to FIFO", policy.value)
            chosen = ordered[0]
            chosen_reason = f"unknown policy {policy.value}; fell back to FIFO"

        eligible_ids = {node.node_id for node in ordered}
        candidates_blob: List[Dict[str, Any]] = []
        for node in sorted(all_nodes, key=lambda n: n.node_id):
            entry: Dict[str, Any] = {
                "node_id": node.node_id,
                "gpu_count": node.gpu_count,
                "available_gpu": node.allocatable_gpu_count,
                "avg_utilization": round(node.avg_utilization, 3),
                "partitions": list(node.partitions),
                "state": node.state,
                "eligible": node.node_id in eligible_ids,
                "selected": node.node_id == chosen.node_id,
            }
            if node.node_id not in eligible_ids:
                entry["rejected_reason"] = self._rejection_reason(spec, node, partition)
            if policy == SchedulerPolicy.BINPACK and node.node_id in eligible_ids:
                entry["score"] = node.allocatable_gpu_count - spec.gpus
            candidates_blob.append(entry)

        decision: Dict[str, Any] = {
            "policy": policy.value,
            "partition": partition,
            "requested_gpus": spec.gpus,
            "chosen_node_id": chosen.node_id,
            "chosen_reason": chosen_reason,
            "candidates": candidates_blob,
            "decided_at": time.time(),
        }
        if rr_pointer is not None:
            decision["round_robin_pointer"] = rr_pointer

        return chosen.node_id, decision

    def _rejection_reason(
        self,
        spec: JobSpec,
        node: NodeCandidate,
        desired_partition: Optional[str],
    ) -> str:
        if not self._is_node_schedulable(node):
            return f"state={node.state or 'unknown'}"
        if not self._node_matches_partition(node, desired_partition):
            return f"partition mismatch (need {desired_partition}, have {','.join(node.partitions) or '-'})"
        if node.allocatable_gpu_count < spec.gpus:
            return f"not enough GPUs ({node.allocatable_gpu_count} available, {spec.gpus} requested)"
        return "ineligible"

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
                labels = node.labels or {}
                candidates.append(
                    NodeCandidate(
                        node_id=node.node_id,
                        gpu_count=len(gpu_entries),
                        avg_utilization=avg_utilization,
                        partitions=self._parse_partitions(labels.get("partition")),
                        state=str(labels.get("state") or ""),
                        available_gpu_count=self._available_gpu_count(
                            labels=labels,
                            total_gpu_count=len(gpu_entries),
                            state=str(labels.get("state") or ""),
                        ),
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
            labels = node.labels or {}
            candidates.append(
                NodeCandidate(
                    node_id=node.node_id,
                    gpu_count=len(gpu_entries),
                    avg_utilization=avg_utilization,
                    partitions=self._parse_partitions(labels.get("partition")),
                    state=str(labels.get("state") or ""),
                    available_gpu_count=self._available_gpu_count(
                        labels=labels,
                        total_gpu_count=len(gpu_entries),
                        state=str(labels.get("state") or ""),
                    ),
                )
            )
        return candidates

    def _eligible_nodes(self, spec: JobSpec, nodes: List[NodeCandidate]) -> List[NodeCandidate]:
        desired_partition = self._desired_partition(spec)
        return [
            node
            for node in nodes
            if node.allocatable_gpu_count >= spec.gpus
            and self._node_matches_partition(node, desired_partition)
            and self._is_node_schedulable(node)
        ]

    def _desired_partition(self, spec: JobSpec) -> Optional[str]:
        preferred = (spec.metadata or {}).get("partition")
        if preferred:
            return str(preferred)
        default_partition = getattr(self.backend, "default_partition", None)
        if default_partition:
            return str(default_partition)
        return None

    @staticmethod
    def _node_matches_partition(node: NodeCandidate, desired_partition: Optional[str]) -> bool:
        if not desired_partition:
            return True
        desired = desired_partition.strip().lower()
        return any(partition.lower() == desired for partition in node.partitions)

    @staticmethod
    def _is_node_schedulable(node: NodeCandidate) -> bool:
        raw_state = (node.state or "").strip().lower()
        if not raw_state:
            return True

        blocked_prefixes = (
            "down",
            "drain",
            "drng",
            "drained",
            "fail",
            "failing",
            "maint",
            "power",
            "future",
            "unk",
            "resv",
            "reserved",
            "not_responding",
        )
        # Slurm JSON output may be normalized into strings like
        # "base=idle,flags=['DRAIN']". Extract word-like tokens so blocked
        # states remain detectable even after that serialization step.
        tokens = [token.strip("*~#! ") for token in re.findall(r"[a-z_]+", raw_state) if token.strip()]
        return not any(token.startswith(blocked_prefixes) for token in tokens)

    @staticmethod
    def _parse_partitions(value: object) -> tuple[str, ...]:
        raw = str(value or "")
        if not raw:
            return ()
        return tuple(partition.strip() for partition in raw.split(",") if partition.strip())

    @staticmethod
    def _available_gpu_count(labels: dict, total_gpu_count: int, state: str) -> int:
        raw_available = labels.get("gpu_available")
        if raw_available not in (None, ""):
            try:
                return max(int(str(raw_available)), 0)
            except ValueError:
                pass

        raw_state = (state or "").strip().lower()
        if re.search(r"\b(alloc|allocated|mix|mixed|comp|completing)\b", raw_state):
            return 0

        return total_gpu_count
