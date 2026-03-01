import logging
import time
from typing import List

from control_plane.core.persistence import pg_conn, redis_client

logger = logging.getLogger("control_plane.scheduler")

_ASSIGN_Q_PREFIX = "assign:"
_QUEUE_KEY = "jobs:queue"


class NaiveScheduler:
    """
    Milestone 4 scheduler:
    - Pops jobs FIFO from the global queue
    - Round-robins across nodes seen in the last `recent_secs`
    - Pushes onto per-node assign queues and marks DB status PLACED
    """

    def __init__(self, loop_secs: int = 1, recent_secs: int = 30):
        self.loop_secs = loop_secs
        self.recent_secs = recent_secs

    def tick(self):
        r = redis_client()
        job_id = r.lpop(_QUEUE_KEY)
        if not job_id:
            return

        nodes = self._recent_nodes(self.recent_secs)
        if not nodes:
            # No nodes; push the job back and retry later
            r.lpush(_QUEUE_KEY, job_id)
            return

        idx = (int(r.incr("rr:idx")) - 1) % len(nodes)
        node_id = nodes[idx]
        r.rpush(f"{_ASSIGN_Q_PREFIX}{node_id}", job_id)

        with pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE jobs SET status=%s, node_id=%s WHERE job_id=%s",
                    ("PLACED", node_id, job_id),
                )
                conn.commit()

        logger.info("Placed job %s on node %s", job_id, node_id)

    def _recent_nodes(self, seconds: int) -> List[str]:
        with pg_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT node_id FROM nodes WHERE last_seen > NOW() - INTERVAL %s ORDER BY node_id",
                    (f"{seconds} seconds",),
                )
                rows = cur.fetchall()
                return [row[0] for row in rows]
