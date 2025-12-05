import logging
import threading
import time
from typing import Optional

from control_plane.core.models import JobSpec, JobState, NodeInfo
from control_plane.core import persistence

logger = logging.getLogger("control_plane.scheduler")


class Scheduler:
    """
    Simple FIFO / round-robin scheduler that assigns jobs to active nodes.
    Prefers nodes advertising real GPUs (labels.has_real_gpu == "true") when available.
    """

    def __init__(
        self,
        *,
        poll_timeout: int = 5,
        idle_sleep: float = 2.0,
        node_ttl: float = 30.0,
    ):
        self.poll_timeout = poll_timeout
        self.idle_sleep = idle_sleep
        self.node_ttl = node_ttl
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._rr_index = -1

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        logger.info("Starting scheduler loop...")
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run(self):
        while not self._stop_event.is_set():
            job_id = persistence.dequeue_job(timeout=self.poll_timeout)
            if not job_id:
                continue
            spec = persistence.get_job_spec(job_id)
            if spec is None:
                logger.warning("Missing spec for job %s; skipping", job_id)
                continue
            node = self._choose_node(spec)
            if node is None:
                logger.warning("No active nodes; requeuing job %s", job_id)
                persistence.requeue_job(job_id)
                time.sleep(self.idle_sleep)
                continue
            try:
                persistence.assign_job_to_node(node.node_id, job_id)
                persistence.update_job_state(
                    job_id,
                    JobState.PLACED,
                    node_id=node.node_id,
                    gpu_ids=[],
                )
                logger.info("Placed job %s on node %s", job_id, node.node_id)
            except Exception as exc:
                logger.exception("Failed to place job %s", job_id)
                # Put the job back at the front so it isn't lost.
                persistence.requeue_job(job_id)
                time.sleep(self.idle_sleep)

    def _choose_node(self, spec: JobSpec) -> Optional[NodeInfo]:
        active_nodes = persistence.list_active_nodes(self.node_ttl)
        if not active_nodes:
            return None
        real_nodes = [
            node
            for node in active_nodes
            if node.labels.get("has_real_gpu", "false").lower() == "true"
        ]
        candidates = real_nodes or active_nodes
        self._rr_index = (self._rr_index + 1) % len(candidates)
        return candidates[self._rr_index]
