import json
import logging
import os
import pathlib
import time
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
import redis

from control_plane.core.models import JobSpec, JobState, JobStatus, NodeInfo

logger = logging.getLogger("control_plane.persistence")

_SCHEMA_PATH = pathlib.Path(__file__).resolve().parents[1] / "db" / "schema.sql"
_QUEUE_KEY = "jobs:queue"
_SPEC_KEY_PREFIX = "jobs:spec:"


def pg_conn():
    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "overlay"),
        user=os.getenv("POSTGRES_USER", "overlay"),
        password=os.getenv("POSTGRES_PASSWORD", "overlay"),
    )
    conn.autocommit = True
    return conn


def redis_client():
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=int(os.getenv("REDIS_DB", "0")),
        decode_responses=True,
    )


def bootstrap_storage():
    """
    Run schema migrations / bootstrap logic at startup.
    """
    logger.info("Ensuring schema exists via %s", _SCHEMA_PATH)
    schema_sql = _SCHEMA_PATH.read_text()
    with pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(schema_sql)


def check_postgres_ready() -> Tuple[bool, Dict[str, Any]]:
    conn = None
    try:
        conn = pg_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT version();")
            version_row = cur.fetchone()
        info = {
            "host": os.getenv("POSTGRES_HOST", "postgres"),
            "port": int(os.getenv("POSTGRES_PORT", "5432")),
            "db": os.getenv("POSTGRES_DB", "overlay"),
            "server_version": version_row[0] if version_row else None,
        }
        return True, info
    except Exception as exc:
        logger.exception("Postgres readiness check failed")
        return False, {"error": str(exc)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def check_postgres() -> bool:
    ok, _ = check_postgres_ready()
    return ok


def check_redis_ready() -> Tuple[bool, Dict[str, Any]]:
    try:
        r = redis_client()
        pong = r.ping()
        info = {
            "host": os.getenv("REDIS_HOST", "redis"),
            "port": int(os.getenv("REDIS_PORT", "6379")),
            "pong": bool(pong),
        }
        return True, info
    except Exception as exc:
        logger.exception("Redis readiness check failed")
        return False, {"error": str(exc)}


def check_redis() -> bool:
    ok, _ = check_redis_ready()
    return ok


def ready_report() -> Dict[str, Any]:
    ok_pg, pg_info = check_postgres_ready()
    ok_redis, redis_info = check_redis_ready()
    ok = ok_pg and ok_redis
    return {
        "ok": ok,
        "postgres": {"ok": ok_pg, **pg_info},
        "redis": {"ok": ok_redis, **redis_info},
    }


def enqueue_job(spec: JobSpec) -> JobStatus:
    if not spec.job_id:
        raise ValueError("job_id is required")

    serialized_spec = spec.model_dump()
    enqueued_ts = time.time()

    with pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO jobs (job_id, spec, status, timestamps)
                VALUES (%s, %s::jsonb, %s, %s::jsonb)
                ON CONFLICT (job_id) DO UPDATE SET spec=EXCLUDED.spec
                """,
                (
                    spec.job_id,
                    json.dumps(serialized_spec),
                    JobState.QUEUED.value,
                    json.dumps({"enqueued": enqueued_ts}),
                ),
            )

    r = redis_client()
    r.rpush(_QUEUE_KEY, spec.job_id)
    r.set(f"{_SPEC_KEY_PREFIX}{spec.job_id}", json.dumps(serialized_spec))

    return JobStatus(
        state=JobState.QUEUED,
        node_id=None,
        gpu_ids=[],
        timestamps={"enqueued": enqueued_ts},
    )


def get_job_status(job_id: str) -> Optional[JobStatus]:
    with pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, node_id, gpu_ids, timestamps, exit_code, reason FROM jobs WHERE job_id = %s",
                (job_id,),
            )
            row = cur.fetchone()

    if not row:
        return None

    status, node_id, gpu_ids, timestamps, exit_code, reason = row
    return JobStatus(
        state=JobState(status),
        node_id=node_id,
        gpu_ids=list(gpu_ids) if gpu_ids else [],
        timestamps=timestamps or {},
        exit_code=exit_code,
        reason=reason,
    )


def set_job_state(job_id: str, state: str, exit_code: Optional[int] = None) -> None:
    """
    Transition a job to a new state and append timestamp. Used by agents and scheduler.
    """
    if not job_id:
        raise ValueError("job_id is required")
    if state not in JobState._value2member_map_:  # type: ignore[attr-defined]
        raise ValueError(f"Invalid state: {state}")

    ts_key = state.lower()
    ts_value = time.time()
    with pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE jobs
                SET status=%s,
                    exit_code=%s,
                    timestamps = coalesce(timestamps, '{}'::jsonb) || %s::jsonb
                WHERE job_id=%s
                """,
                (state, exit_code, json.dumps({ts_key: ts_value}), job_id),
            )


def upsert_node(node: NodeInfo) -> None:
    """
    Insert or update a node heartbeat payload.
    """
    if not node.node_id:
        raise ValueError("node_id is required")
    serialized_gpus = json.dumps([gpu.model_dump() for gpu in node.gpus])
    labels_json = json.dumps(node.labels or {})
    agent_health_json = json.dumps(node.agent_health or {})

    with pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO nodes (node_id, labels, gpus, agent_health, last_seen)
                VALUES (%s, %s::jsonb, %s::jsonb, %s::jsonb, NOW())
                ON CONFLICT (node_id) DO UPDATE
                    SET labels=EXCLUDED.labels,
                        gpus=EXCLUDED.gpus,
                        agent_health=EXCLUDED.agent_health,
                        last_seen=EXCLUDED.last_seen
                """,
                (node.node_id, labels_json, serialized_gpus, agent_health_json),
            )


def list_nodes() -> List[NodeInfo]:
    """
    Fetch the current known nodes ordered by id.
    """
    with pg_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    node_id,
                    labels,
                    gpus,
                    agent_health,
                    EXTRACT(EPOCH FROM last_seen) AS last_seen
                FROM nodes
                ORDER BY node_id
                """
            )
            rows = cur.fetchall()

    nodes: List[NodeInfo] = []
    for row in rows:
        nodes.append(
            NodeInfo(
                node_id=row["node_id"],
                labels=row.get("labels") or {},
                gpus=row.get("gpus") or [],
                agent_health=row.get("agent_health") or {},
                last_seen=row.get("last_seen"),
            )
        )
    return nodes
