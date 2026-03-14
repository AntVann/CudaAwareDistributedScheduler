import json
import logging
import math
import os
import pathlib
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional, Tuple

try:
    import psycopg2
    import psycopg2.extras
except Exception:  # pragma: no cover - only hit when optional dependency is absent
    psycopg2 = None  # type: ignore[assignment]

try:
    import redis
except Exception:  # pragma: no cover - only hit when optional dependency is absent
    redis = None  # type: ignore[assignment]

from control_plane.core.models import JobSpec, JobState, JobStatus, NodeInfo, SchedulerPolicy

logger = logging.getLogger("control_plane.persistence")

_SCHEMA_PATH = pathlib.Path(__file__).resolve().parents[1] / "db" / "schema.sql"
_QUEUE_KEY = "jobs:queue"
_SPEC_KEY_PREFIX = "jobs:spec:"
_SCHEDULER_SETTINGS_KEY = "active"
_DEFAULT_SQLITE_PATH = "~/scheduler.db"
_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  spec TEXT NOT NULL,
  status TEXT NOT NULL,
  backend_ref TEXT,
  node_id TEXT,
  gpu_ids TEXT,
  timestamps TEXT,
  exit_code INTEGER,
  reason TEXT
);

CREATE TABLE IF NOT EXISTS nodes (
  node_id TEXT PRIMARY KEY,
  labels TEXT,
  gpus TEXT,
  agent_health TEXT,
  last_seen REAL
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  job_id TEXT,
  kind TEXT NOT NULL,
  payload TEXT
);

CREATE TABLE IF NOT EXISTS scheduler_settings (
  singleton_key TEXT PRIMARY KEY,
  active_policy TEXT NOT NULL,
  updated_at REAL NOT NULL,
  updated_by TEXT
);
"""


class _MemoryRedis:
    def __init__(self) -> None:
        self._lists: Dict[str, List[str]] = {}
        self._kv: Dict[str, str] = {}
        self._counters: Dict[str, int] = {}
        self._lock = threading.Lock()

    def ping(self) -> bool:
        return True

    def rpush(self, key: str, value: str) -> int:
        with self._lock:
            values = self._lists.setdefault(key, [])
            values.append(value)
            return len(values)

    def lpop(self, key: str) -> Optional[str]:
        with self._lock:
            values = self._lists.get(key)
            if not values:
                return None
            return values.pop(0)

    def lpush(self, key: str, value: str) -> int:
        with self._lock:
            values = self._lists.setdefault(key, [])
            values.insert(0, value)
            return len(values)

    def incr(self, key: str) -> int:
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1
            return self._counters[key]

    def set(self, key: str, value: str) -> bool:
        with self._lock:
            self._kv[key] = value
            return True

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            return self._kv.get(key)

    def llen(self, key: str) -> int:
        with self._lock:
            return len(self._lists.get(key, []))


_MEMORY_REDIS = _MemoryRedis()


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


def _database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def _use_sqlite() -> bool:
    return _database_url().startswith("sqlite://")


def _sqlite_path() -> pathlib.Path:
    raw = _database_url()
    if raw.startswith("sqlite:///"):
        path_str = raw[len("sqlite:///") :]
    elif raw.startswith("sqlite://"):
        path_str = raw[len("sqlite://") :]
    else:
        path_str = _DEFAULT_SQLITE_PATH
    return pathlib.Path(path_str).expanduser().resolve()


def _queue_backend() -> str:
    default_backend = "memory" if _use_sqlite() and os.getenv("BACKEND", "").strip().lower() == "slurm" else "redis"
    return (os.getenv("QUEUE_BACKEND", default_backend).strip().lower() or default_backend)


def _use_memory_queue() -> bool:
    return _queue_backend() == "memory"


@contextmanager
def _sqlite_conn() -> Any:
    db_path = _sqlite_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _json_load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _to_sqlite_job_status(row: Any) -> JobStatus:
    return JobStatus(
        state=JobState(row["status"]),
        node_id=row["node_id"],
        gpu_ids=list(_json_load(row["gpu_ids"], [])),
        timestamps=_json_load(row["timestamps"], {}),
        exit_code=row["exit_code"],
        reason=row["reason"],
    )


def pg_conn():
    if _use_sqlite():
        raise RuntimeError("pg_conn is unavailable when DATABASE_URL points to sqlite")
    if psycopg2 is None:
        raise RuntimeError("psycopg2 is required for postgres mode")
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
    if _use_memory_queue():
        return _MEMORY_REDIS
    if redis is None:
        raise RuntimeError("redis-py is required for redis queue mode")
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
    if _use_sqlite():
        logger.info("Ensuring sqlite schema exists at %s", _sqlite_path())
        with _sqlite_conn() as conn:
            conn.executescript(_SQLITE_SCHEMA)
        return

    logger.info("Ensuring schema exists via %s", _SCHEMA_PATH)
    schema_sql = _SCHEMA_PATH.read_text()
    with pg_conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(schema_sql)


def check_postgres_ready() -> Tuple[bool, Dict[str, Any]]:
    if _use_sqlite():
        path = _sqlite_path()
        try:
            with _sqlite_conn() as conn:
                row = conn.execute("SELECT sqlite_version()").fetchone()
            return True, {"mode": "sqlite", "path": str(path), "server_version": row[0] if row else None}
        except Exception as exc:
            logger.exception("SQLite readiness check failed")
            return False, {"mode": "sqlite", "path": str(path), "error": str(exc)}

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
    if _use_memory_queue():
        return True, {"mode": "memory", "pong": True}

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

    if _use_sqlite():
        with _sqlite_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT OR IGNORE INTO jobs (job_id, spec, status, timestamps, gpu_ids)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    spec.job_id,
                    json.dumps(serialized_spec),
                    JobState.QUEUED.value,
                    json.dumps({"enqueued": enqueued_ts}),
                    json.dumps([]),
                ),
            )
            created = cur.rowcount > 0
            if not created:
                existing = conn.execute(
                    """
                    SELECT status, node_id, gpu_ids, timestamps, exit_code, reason
                    FROM jobs
                    WHERE job_id = ?
                    """,
                    (spec.job_id,),
                ).fetchone()
                if existing is None:
                    raise RuntimeError(f"Job {spec.job_id} disappeared after duplicate check")
                logger.info("Job %s already exists; returning existing status", spec.job_id)
                return _to_sqlite_job_status(existing), False

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
    if _use_sqlite():
        with _sqlite_conn() as conn:
            row = conn.execute(
                "SELECT status, node_id, gpu_ids, timestamps, exit_code, reason FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return _to_sqlite_job_status(row) if row else None

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

    if _use_sqlite():
        with _sqlite_conn() as conn:
            row = conn.execute("SELECT spec FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    else:
        with pg_conn() as conn:
            with _cursor(conn) as cur:
                cur.execute("SELECT spec FROM jobs WHERE job_id = %s", (job_id,))
                row = cur.fetchone()

    if not row:
        return None

    if isinstance(row, sqlite3.Row):
        # sqlite3.Row is indexable; use explicit key access for clarity.
        spec_payload = row["spec"]
    elif isinstance(row, dict):
        spec_payload = row.get("spec")
    else:
        spec_payload = row[0]
    if spec_payload is None:
        return None
    if isinstance(spec_payload, str):
        spec_payload = json.loads(spec_payload)
    return JobSpec.model_validate(spec_payload)


def place_job(job_id: str, node_id: str) -> None:
    if _use_sqlite():
        ts = time.time()
        with _sqlite_conn() as conn:
            row = conn.execute("SELECT timestamps FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(f"Job {job_id} not found")
            timestamps = _json_load(row["timestamps"], {})
            timestamps["placed"] = ts
            conn.execute(
                """
                UPDATE jobs
                SET status=?, node_id=?, timestamps=?
                WHERE job_id=?
                """,
                (JobState.PLACED.value, node_id, json.dumps(timestamps), job_id),
            )
        return

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


def store_backend_ref(job_id: str, backend_ref: str) -> None:
    if not job_id:
        raise ValueError("job_id is required")
    if not backend_ref:
        raise ValueError("backend_ref is required")
    if _use_sqlite():
        with _sqlite_conn() as conn:
            cur = conn.execute(
                "UPDATE jobs SET backend_ref = ? WHERE job_id = ?",
                (backend_ref, job_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"Job {job_id} not found")
        return

    with pg_conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "UPDATE jobs SET backend_ref = %s WHERE job_id = %s",
                (backend_ref, job_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"Job {job_id} not found")


def get_backend_ref(job_id: str) -> Optional[str]:
    if _use_sqlite():
        with _sqlite_conn() as conn:
            row = conn.execute("SELECT backend_ref FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return row["backend_ref"] if row else None

    with pg_conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("SELECT backend_ref FROM jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()

    if not row:
        return None
    return row[0] if not isinstance(row, dict) else row.get("backend_ref")


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

    if _use_sqlite():
        with _sqlite_conn() as conn:
            row = conn.execute("SELECT timestamps FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(f"Job {job_id} not found")
            timestamps = _json_load(row["timestamps"], {})
            timestamps[ts_key] = ts_value
            cur = conn.execute(
                """
                UPDATE jobs
                SET status=?, exit_code=?, reason=?, timestamps=?
                WHERE job_id=?
                """,
                (state, exit_code, reason, json.dumps(timestamps), job_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"Job {job_id} not found")
        return

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

    if _use_sqlite():
        with _sqlite_conn() as conn:
            conn.execute(
                """
                INSERT INTO nodes (node_id, labels, gpus, agent_health, last_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(node_id) DO UPDATE SET
                    labels=excluded.labels,
                    gpus=excluded.gpus,
                    agent_health=excluded.agent_health,
                    last_seen=excluded.last_seen
                """,
                (node.node_id, labels_json, serialized_gpus, agent_health_json, time.time()),
            )
        return

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
    if _use_sqlite():
        with _sqlite_conn() as conn:
            rows = conn.execute(
                """
                SELECT job_id, status, backend_ref, node_id, gpu_ids, timestamps, exit_code, reason
                FROM jobs
                """
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            timestamps = _json_load(row["timestamps"], {})
            result.append(
                {
                    "job_id": row["job_id"],
                    "state": row["status"],
                    "backend_ref": row["backend_ref"],
                    "node_id": row["node_id"],
                    "gpu_ids": list(_json_load(row["gpu_ids"], [])),
                    "timestamps": timestamps,
                    "exit_code": row["exit_code"],
                    "reason": row["reason"],
                }
            )
        result.sort(key=lambda item: float((item.get("timestamps") or {}).get("enqueued", 0.0)), reverse=True)
        return result

    with pg_conn() as conn:
        with _cursor(conn, cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT job_id, status, backend_ref, node_id, gpu_ids, timestamps, exit_code, reason
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
                "backend_ref": row.get("backend_ref"),
                "node_id": row["node_id"],
                "gpu_ids": list(row["gpu_ids"]) if row["gpu_ids"] else [],
                "timestamps": row["timestamps"] or {},
                "exit_code": row["exit_code"],
                "reason": row["reason"],
            }
        )
    return result


def get_jobs_in_states(states: List[str]) -> List[Dict[str, Any]]:
    if not states:
        return []

    if _use_sqlite():
        placeholders = ",".join("?" for _ in states)
        with _sqlite_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT job_id, status, backend_ref
                FROM jobs
                WHERE status IN ({placeholders})
                ORDER BY job_id
                """,
                tuple(states),
            ).fetchall()
        return [
            {
                "job_id": row["job_id"],
                "status": row["status"],
                "backend_ref": row["backend_ref"],
            }
            for row in rows
        ]

    with pg_conn() as conn:
        with _cursor(conn, cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT job_id, status, backend_ref
                FROM jobs
                WHERE status = ANY(%s)
                ORDER BY job_id
                """,
                (states,),
            )
            rows = cur.fetchall()

    return [
        {
            "job_id": row["job_id"],
            "status": row["status"],
            "backend_ref": row.get("backend_ref"),
        }
        for row in rows
    ]


def job_summary() -> Dict[str, int]:
    """
    Return aggregate job counts by state for the dashboard.
    """
    if _use_sqlite():
        with _sqlite_conn() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS cnt FROM jobs GROUP BY status").fetchall()
    else:
        with pg_conn() as conn:
            with _cursor(conn, cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT status, COUNT(*) AS cnt FROM jobs GROUP BY status")
                rows = cur.fetchall()

    counts = _empty_job_counts()
    for row in rows:
        key = str(row["status"]).lower()
        if key in counts:
            counts[key] = int(row["cnt"])
    return counts


def list_nodes() -> List[NodeInfo]:
    """
    Fetch the current known nodes ordered by id.
    """
    if _use_sqlite():
        with _sqlite_conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    node_id,
                    labels,
                    gpus,
                    agent_health,
                    last_seen
                FROM nodes
                ORDER BY node_id
                """
            ).fetchall()
    else:
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
        labels = row["labels"] if not _use_sqlite() else _json_load(row["labels"], {})
        gpus = row["gpus"] if not _use_sqlite() else _json_load(row["gpus"], [])
        agent_health = row["agent_health"] if not _use_sqlite() else _json_load(row["agent_health"], {})
        nodes.append(
            NodeInfo(
                node_id=row["node_id"],
                labels=labels or {},
                gpus=gpus or [],
                agent_health=agent_health or {},
                last_seen=row["last_seen"],
            )
        )
    return nodes


def get_active_policy() -> SchedulerPolicy:
    if _use_sqlite():
        with _sqlite_conn() as conn:
            row = conn.execute(
                "SELECT active_policy FROM scheduler_settings WHERE singleton_key = ?",
                (_SCHEDULER_SETTINGS_KEY,),
            ).fetchone()
    else:
        with pg_conn() as conn:
            with _cursor(conn) as cur:
                cur.execute(
                    "SELECT active_policy FROM scheduler_settings WHERE singleton_key = %s",
                    (_SCHEDULER_SETTINGS_KEY,),
                )
                row = cur.fetchone()

    if row:
        if isinstance(row, sqlite3.Row):
            stored_value = row["active_policy"]
        elif isinstance(row, dict):
            stored_value = row.get("active_policy")
        else:
            stored_value = row[0]
        stored_policy = _coerce_policy(stored_value)
        if stored_policy is not None:
            return stored_policy
        logger.warning("Ignoring invalid stored scheduler policy %r", stored_value)

    seeded_policy = _default_policy()
    if _use_sqlite():
        with _sqlite_conn() as conn:
            conn.execute(
                """
                INSERT INTO scheduler_settings (singleton_key, active_policy, updated_at, updated_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(singleton_key) DO UPDATE SET
                    active_policy=excluded.active_policy,
                    updated_at=excluded.updated_at,
                    updated_by=excluded.updated_by
                """,
                (_SCHEDULER_SETTINGS_KEY, seeded_policy.value, time.time(), "startup"),
            )
    else:
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

    if _use_sqlite():
        with _sqlite_conn() as conn:
            conn.execute(
                """
                INSERT INTO scheduler_settings (singleton_key, active_policy, updated_at, updated_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(singleton_key) DO UPDATE SET
                    active_policy=excluded.active_policy,
                    updated_at=excluded.updated_at,
                    updated_by=excluded.updated_by
                """,
                (_SCHEDULER_SETTINGS_KEY, normalized.value, time.time(), updated_by),
            )
    else:
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

    if _use_sqlite():
        fresh_cutoff = time.time() - fresh_node_seconds
        with _sqlite_conn() as conn:
            node_row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN last_seen > ? THEN 1 ELSE 0 END) AS fresh
                FROM nodes
                """,
                (fresh_cutoff,),
            ).fetchone()
            raw_job_rows = conn.execute(
                """
                SELECT status, timestamps
                FROM jobs
                WHERE timestamps IS NOT NULL
                """
            ).fetchall()
        job_rows = []
        for row in raw_job_rows:
            timestamps = _json_load(row["timestamps"], {})
            placed = float(timestamps.get("placed", -1))
            running = timestamps.get("running")
            done = float(timestamps.get("done", -1))
            failed = float(timestamps.get("failed", -1))
            cancelled = float(timestamps.get("cancelled", -1))
            terminal_after_window = done >= window_start or failed >= window_start or cancelled >= window_start
            include = (
                placed >= window_start
                or (running is not None and terminal_after_window)
                or terminal_after_window
            )
            if include:
                job_rows.append({"status": row["status"], "timestamps": timestamps})
    else:
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

    total_nodes = int(node_row["total"] or 0)
    fresh_nodes = int(node_row["fresh"] or 0)
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
