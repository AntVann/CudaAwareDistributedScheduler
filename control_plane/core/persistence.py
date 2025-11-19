import json
import logging
import os
import pathlib
import time
from typing import Any, Dict, Optional, Tuple

import psycopg2
import psycopg2.extras
import redis

from control_plane.core.models import JobSpec, JobState, JobStatus

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
