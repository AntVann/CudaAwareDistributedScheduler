import logging
import time
from typing import List, Optional

from control_plane.core.backend import ExecutionBackend
from control_plane.core.models import JobSpec, JobStatus, NodeInfo
from control_plane.core.persistence import get_job_status, list_nodes as persist_list_nodes, redis_client

logger = logging.getLogger("control_plane.backends.redis_agent")

_ASSIGN_Q_PREFIX = "assign:"


class RedisAgentBackend(ExecutionBackend):
    """
    Wraps the existing Redis + Agent dispatch mechanism.
    Zero logic changes — this is a pure refactor of the original inline code.
    """

    def submit(self, spec: JobSpec, node_hint: Optional[str] = None) -> str:
        """
        Push the job onto the per-node assignment queue in Redis.
        The agent polling that queue will pick it up.
        """
        if not node_hint:
            raise ValueError("RedisAgentBackend requires a node_hint for dispatch")
        r = redis_client()
        r.rpush(f"{_ASSIGN_Q_PREFIX}{node_hint}", spec.job_id)
        return spec.job_id

    def poll_status(self, job_id: str) -> Optional[JobStatus]:
        """
        Read job state from Postgres. Agents POST state updates via the
        /api/admin/jobs/{job_id}/state endpoint which writes to Postgres.
        """
        return get_job_status(job_id)

    def list_nodes(self, recent_secs: int = 30) -> List[NodeInfo]:
        """
        Query Postgres for nodes with recent heartbeats.
        Same query as the original _recent_nodes() but returns full NodeInfo.
        """
        cutoff = time.time() - recent_secs
        nodes: List[NodeInfo] = []
        for node in persist_list_nodes():
            if float(node.last_seen or 0.0) < cutoff:
                continue
            gpu_entries = node.gpus if isinstance(node.gpus, list) else []
            # Tolerate partial/malformed GPU dicts from DB — skip invalid entries
            safe_gpus = []
            for gpu in gpu_entries:
                if isinstance(gpu, dict):
                    try:
                        from control_plane.core.models import GpuInfo
                        safe_gpus.append(GpuInfo(**gpu))
                    except Exception:
                        logger.debug("Skipping malformed GPU entry on node %s: %s", node.node_id, gpu)
                elif hasattr(gpu, "index"):
                    safe_gpus.append(gpu)
            nodes.append(
                NodeInfo(
                    node_id=node.node_id,
                    gpus=safe_gpus,
                    labels=node.labels or {},
                    agent_health=node.agent_health or {},
                    last_seen=node.last_seen,
                )
            )
        return nodes

    def cancel(self, job_id: str) -> bool:
        """Not implemented for the agent backend yet."""
        logger.warning("Cancel not implemented for RedisAgentBackend (job %s)", job_id)
        return False
