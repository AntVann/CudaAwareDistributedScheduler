import json
import logging
import math
import os
import pathlib
import time
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
import redis

from control_plane.core.models import JobSpec, JobState, JobStatus, NodeInfo, SchedulerPolicy

logger = logging.getLogger("control_plane.persistence")

_SCHEMA_PATH = pathlib.Path(__file__).resolve().parents[1] / "db" / "schema.sql"
_QUEUE_KEY = "jobs:queue"
_SPEC_KEY_PREFIX = "jobs:spec:"
_SCHEDULER_SETTINGS_KEY = "active"


def _job_status_from_row(row: tuple[Any, ...]) -> JobStatus:
    status, node_id, gpu_ids, timestamps, exit_code, reason = row
    return JobStatus(
        state=JobState(status),
        node_id=node_id,
        gpu_ids=list(gpu_ids) if gpu_ids else [],
        timestamps=timestamps or {},
        exit_code=exit_code,
        reason=reason,
    )


def _cursor(conn: Any, cursor_factory: Any | None = None) -> Any:
    if cursor_factory is None:
        return conn.cursor()
    try:
        return conn.cursor(cursor_factory=cursor_factory)
    except TypeError:
        return conn.cursor()


def _supported_policy_values() -> List[str]:
    return [policy.value for policy in SchedulerPolicy]


def _coerce_policy(value: str | None) -> SchedulerPolicy | None:
    normalized = (value or "").strip().upper()
    if normalized in SchedulerPolicy._value2member_map_:  # type: ignore[attr-defined]
        return SchedulerPolicy(normalized)
    return None


def _default_policy() -> SchedulerPolicy:
    return _coerce_policy(os.getenv("SCHED_POLICY")) or SchedulerPolicy.FIFO


def _empty_job_counts() -> Dict[str, int]:
    return {state.value.lower(): 0 for state in JobState}


def _terminal_timestamp(timestamps: Dict[str, Any]) -> float | None:
    for key in ("done", "failed", "cancelled"):
        value = timestamps.get(key)
        if value is not None:
            return float(value)
    return None


def _percentile(values: List[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    lower = ordered[low]
    upper = ordered[high]
    interpolated = lower + (upper - lower) * (rank - low)
    return int(round(interpolated))


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
        with _cursor(conn) as cur:
            cur.execute(schema_sql)


def check_postgres_ready() -> Tuple[bool, Dict[str, Any]]:
    conn = None
    try:
        conn = pg_conn()
        with _cursor(conn, cursor_factory=psycopg2.extras.DictCursor) as cur:
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


def enqueue_job(spec: JobSpec) -> tuple[JobStatus, bool]:
    if not spec.job_id:
        raise ValueError("job_id is required")

    serialized_spec = spec.model_dump()
    enqueued_ts = time.time()

    with pg_conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO jobs (job_id, spec, status, timestamps)
                VALUES (%s, %s::jsonb, %s, %s::jsonb)
                ON CONFLICT (job_id) DO NOTHING
                RETURNING status, node_id, gpu_ids, timestamps, exit_code, reason
                """,
                (
                    spec.job_id,
                    json.dumps(serialized_spec),
                    JobState.QUEUED.value,
                    json.dumps({"enqueued": enqueued_ts}),
                ),
            )
            row = cur.fetchone()

            if row is None:
                cur.execute(
                    """
                    SELECT status, node_id, gpu_ids, timestamps, exit_code, reason
                    FROM jobs
                    WHERE job_id = %s
                    """,
                    (spec.job_id,),
                )
                existing = cur.fetchone()
                if existing is None:
                    raise RuntimeError(f"Job {spec.job_id} disappeared after duplicate check")
                logger.info("Job %s already exists; returning existing status", spec.job_id)
                return _job_status_from_row(existing), False

    r = redis_client()
    r.rpush(_QUEUE_KEY, spec.job_id)
    r.set(f"{_SPEC_KEY_PREFIX}{spec.job_id}", json.dumps(serialized_spec))

    return (
        JobStatus(
            state=JobState.QUEUED,
            node_id=None,
            gpu_ids=[],
            timestamps={"enqueued": enqueued_ts},
        ),
        True,
    )


def get_job_status(job_id: str) -> Optional[JobStatus]:
    with pg_conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "SELECT status, node_id, gpu_ids, timestamps, exit_code, reason FROM jobs WHERE job_id = %s",
                (job_id,),
            )
            row = cur.fetchone()

    if not row:
        return None

    return _job_status_from_row(row)


def get_job_spec(job_id: str) -> Optional[JobSpec]:
    r = redis_client()
    spec_raw = r.get(f"{_SPEC_KEY_PREFIX}{job_id}")
    if spec_raw:
        return JobSpec.model_validate(json.loads(spec_raw))

    with pg_conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("SELECT spec FROM jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()

    if not row:
        return None

    spec_payload = row[0] if not isinstance(row, dict) else row.get("spec")
    if spec_payload is None:
        return None
    if isinstance(spec_payload, str):
        spec_payload = json.loads(spec_payload)
    return JobSpec.model_validate(spec_payload)


def place_job(job_id: str, node_id: str) -> None:
    with pg_conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                """
                UPDATE jobs
                SET status=%s,
                    node_id=%s,
                    timestamps = coalesce(timestamps, '{}'::jsonb) || %s::jsonb
                WHERE job_id=%s
                """,
                ("PLACED", node_id, json.dumps({"placed": time.time()}), job_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"Job {job_id} not found")


def set_job_state(
    job_id: str,
    state: str,
    exit_code: Optional[int] = None,
    reason: Optional[str] = None,
) -> None:
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
        with _cursor(conn) as cur:
            cur.execute(
                """
                UPDATE jobs
                SET status=%s,
                    exit_code=%s,
                    reason=%s,
                    timestamps = coalesce(timestamps, '{}'::jsonb) || %s::jsonb
                WHERE job_id=%s
                """,
                (state, exit_code, reason, json.dumps({ts_key: ts_value}), job_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"Job {job_id} not found")


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
        with _cursor(conn) as cur:
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


def list_jobs() -> List[Dict[str, Any]]:
    """
    Fetch all jobs ordered by enqueue time descending.
    Returns flat dicts with {job_id, state, node_id, gpu_ids, timestamps, exit_code, reason}.
    """
    with pg_conn() as conn:
        with _cursor(conn, cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT job_id, status, node_id, gpu_ids, timestamps, exit_code, reason
                FROM jobs
                ORDER BY (timestamps->>'enqueued')::float DESC NULLS LAST
                """
            )
            rows = cur.fetchall()

    result: List[Dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "job_id": row["job_id"],
                "state": row["status"],
                "node_id": row["node_id"],
                "gpu_ids": list(row["gpu_ids"]) if row["gpu_ids"] else [],
                "timestamps": row["timestamps"] or {},
                "exit_code": row["exit_code"],
                "reason": row["reason"],
            }
        )
    return result


def job_summary() -> Dict[str, int]:
    """
    Return aggregate job counts by state for the dashboard.
    """
    with pg_conn() as conn:
        with _cursor(conn, cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT status, COUNT(*) AS cnt FROM jobs GROUP BY status")
            rows = cur.fetchall()

    counts = _empty_job_counts()
    for row in rows:
        key = row["status"].lower()
        if key in counts:
            counts[key] = row["cnt"]
    return counts


def list_nodes() -> List[NodeInfo]:
    """
    Fetch the current known nodes ordered by id.
    """
    with pg_conn() as conn:
        with _cursor(conn, cursor_factory=psycopg2.extras.RealDictCursor) as cur:
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


def get_active_policy() -> SchedulerPolicy:
    with pg_conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "SELECT active_policy FROM scheduler_settings WHERE singleton_key = %s",
                (_SCHEDULER_SETTINGS_KEY,),
            )
            row = cur.fetchone()

    if row:
        stored_value = row[0] if not isinstance(row, dict) else row.get("active_policy")
        stored_policy = _coerce_policy(stored_value)
        if stored_policy is not None:
            return stored_policy
        logger.warning("Ignoring invalid stored scheduler policy %r", stored_value)

    seeded_policy = _default_policy()
    with pg_conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO scheduler_settings (singleton_key, active_policy, updated_by)
                VALUES (%s, %s, %s)
                ON CONFLICT (singleton_key) DO UPDATE
                    SET active_policy = EXCLUDED.active_policy,
                        updated_at = NOW(),
                        updated_by = EXCLUDED.updated_by
                """,
                (_SCHEDULER_SETTINGS_KEY, seeded_policy.value, "startup"),
            )
    return seeded_policy


def set_active_policy(policy: str, updated_by: str) -> SchedulerPolicy:
    normalized = _coerce_policy(policy)
    if normalized is None:
        raise ValueError(f"Unsupported policy: {policy}")

    with pg_conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO scheduler_settings (singleton_key, active_policy, updated_by)
                VALUES (%s, %s, %s)
                ON CONFLICT (singleton_key) DO UPDATE
                    SET active_policy = EXCLUDED.active_policy,
                        updated_at = NOW(),
                        updated_by = EXCLUDED.updated_by
                """,
                (_SCHEDULER_SETTINGS_KEY, normalized.value, updated_by),
            )
    return normalized


def read_metrics_summary(window_minutes: int, fresh_node_seconds: int) -> Dict[str, Any]:
    window_start = time.time() - (window_minutes * 60)
    queue_depth = int(redis_client().llen(_QUEUE_KEY))
    current_counts = job_summary()

    with pg_conn() as conn:
        with _cursor(conn, cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE last_seen > NOW() - INTERVAL %s) AS fresh
                FROM nodes
                """,
                (f"{fresh_node_seconds} seconds",),
            )
            node_row = cur.fetchone() or {"total": 0, "fresh": 0}
            cur.execute(
                """
                SELECT status, timestamps
                FROM jobs
                WHERE timestamps IS NOT NULL
                  AND (
                    (timestamps ? 'placed' AND (timestamps->>'placed')::double precision >= %s)
                    OR
                    (
                      (
                        (timestamps ? 'done' AND (timestamps->>'done')::double precision >= %s)
                        OR (timestamps ? 'failed' AND (timestamps->>'failed')::double precision >= %s)
                        OR (timestamps ? 'cancelled' AND (timestamps->>'cancelled')::double precision >= %s)
                      )
                      AND timestamps ? 'running'
                    )
                    OR
                    (
                      (timestamps ? 'done' AND (timestamps->>'done')::double precision >= %s)
                      OR (timestamps ? 'failed' AND (timestamps->>'failed')::double precision >= %s)
                    )
                  )
                """,
                (
                    window_start,
                    window_start,
                    window_start,
                    window_start,
                    window_start,
                    window_start,
                ),
            )
            job_rows = cur.fetchall()

    placement_latencies: List[int] = []
    run_latencies: List[int] = []
    terminal_counts = {"done": 0, "failed": 0}

    for row in job_rows:
        status = row["status"]
        timestamps = row["timestamps"] or {}
        enqueued = timestamps.get("enqueued")
        placed = timestamps.get("placed")
        running = timestamps.get("running")
        terminal_ts = _terminal_timestamp(timestamps)

        if enqueued is not None and placed is not None and float(placed) >= window_start:
            placement_latencies.append(int(round((float(placed) - float(enqueued)) * 1000)))

        if running is not None and terminal_ts is not None and terminal_ts >= window_start:
            run_latencies.append(int(round((terminal_ts - float(running)) * 1000)))

        if status == JobState.DONE.value and terminal_ts is not None and terminal_ts >= window_start:
            terminal_counts["done"] += 1
        if status == JobState.FAILED.value and terminal_ts is not None and terminal_ts >= window_start:
            terminal_counts["failed"] += 1

    total_nodes = int(node_row["total"])
    fresh_nodes = int(node_row["fresh"])
    return {
        "queue_depth": queue_depth,
        "jobs": current_counts,
        "nodes": {
            "total": total_nodes,
            "fresh": fresh_nodes,
            "stale": max(total_nodes - fresh_nodes, 0),
        },
        "latency_ms": {
            "placement_p50": _percentile(placement_latencies, 0.50),
            "placement_p95": _percentile(placement_latencies, 0.95),
            "run_p50": _percentile(run_latencies, 0.50),
            "run_p95": _percentile(run_latencies, 0.95),
        },
        "windowed_terminal_counts": terminal_counts,
        "window_minutes": window_minutes,
    }


def supported_policies() -> List[str]:
    return _supported_policy_values()
