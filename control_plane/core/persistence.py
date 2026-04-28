import hashlib
import json
import logging
import math
import os
import pathlib
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

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

CREATE TABLE IF NOT EXISTS api_tokens (
  id TEXT PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  subject TEXT NOT NULL,
  role TEXT NOT NULL,
  projects TEXT NOT NULL DEFAULT '[]',
  active INTEGER NOT NULL DEFAULT 1,
  expires_at TEXT,
  created_at TEXT NOT NULL,
  created_by TEXT
);

CREATE TABLE IF NOT EXISTS token_requests (
  id TEXT PRIMARY KEY,
  subject_name TEXT NOT NULL,
  email TEXT NOT NULL,
  requested_projects TEXT NOT NULL DEFAULT '[]',
  purpose TEXT NOT NULL,
  status TEXT NOT NULL,
  review_notes TEXT,
  reviewed_by TEXT,
  created_at TEXT NOT NULL,
  reviewed_at TEXT
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
    if len(row) >= 7:
        status, node_id, gpu_ids, timestamps, exit_code, reason, project = row[:7]
    else:
        status, node_id, gpu_ids, timestamps, exit_code, reason = row
        project = "default"
    return JobStatus(
        state=JobState(status),
        project=project,
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


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


def _parse_iso_dt(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _iso_or_none(value: Any) -> Optional[str]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def hash_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_token() -> str:
    return secrets.token_urlsafe(32)


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
    row_keys = row.keys() if hasattr(row, "keys") else []
    spec_payload = _json_load(row["spec"], {}) if "spec" in row_keys else {}
    return JobStatus(
        state=JobState(row["status"]),
        project=spec_payload.get("project", "default"),
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


def ensure_bootstrap_admin_token() -> bool:
    if os.getenv("AUTH_MODE", "none").strip().lower() != "token":
        return False

    bootstrap_token = os.getenv("ADMIN_API_TOKEN", "").strip() or os.getenv("OPERATOR_API_TOKEN", "").strip()

    if _use_sqlite():
        with _sqlite_conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM api_tokens WHERE role = ? AND active = 1", ("admin",)).fetchone()
            admin_count = int(row[0] or 0)
            if admin_count > 0:
                return False

            if not bootstrap_token:
                raise RuntimeError("ADMIN_API_TOKEN is required when no admin token exists")

            conn.execute(
                """
                INSERT OR IGNORE INTO api_tokens
                (id, token_hash, subject, role, projects, active, expires_at, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, 1, NULL, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    hash_token(bootstrap_token),
                    "bootstrap-admin",
                    "admin",
                    json.dumps(["*"]),
                    _utcnow_iso(),
                    "bootstrap",
                ),
            )
            logger.info("Bootstrapped initial admin token from env")
            return True

    with pg_conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("SELECT COUNT(*) FROM api_tokens WHERE role = 'admin' AND active = TRUE")
            admin_count = int(cur.fetchone()[0])
            if admin_count > 0:
                return False

            if not bootstrap_token:
                raise RuntimeError("ADMIN_API_TOKEN is required when no admin token exists")

            cur.execute(
                """
                INSERT INTO api_tokens (id, token_hash, subject, role, projects, active, created_by)
                VALUES (%s, %s, %s, %s, %s::jsonb, TRUE, %s)
                ON CONFLICT (token_hash) DO NOTHING
                """,
                (
                    str(uuid.uuid4()),
                    hash_token(bootstrap_token),
                    "bootstrap-admin",
                    "admin",
                    json.dumps(["*"]),
                    "bootstrap",
                ),
            )
            logger.info("Bootstrapped initial admin token from env")
            return True


def resolve_human_token(plaintext_token: str) -> Optional[Dict[str, Any]]:
    token_hash = hash_token(plaintext_token)
    if _use_sqlite():
        with _sqlite_conn() as conn:
            row = conn.execute(
                """
                SELECT id, subject, role, projects, active, expires_at
                FROM api_tokens
                WHERE token_hash = ?
                """,
                (token_hash,),
            ).fetchone()

        if row is None or not int(row["active"]):
            return None

        expires_at = _parse_iso_dt(row["expires_at"])
        if expires_at is not None and expires_at <= datetime.utcnow():
            return None

        projects = _json_load(row["projects"], [])
        return {
            "token_id": str(row["id"]),
            "subject": row["subject"],
            "role": row["role"],
            "projects": projects,
            "expires_at": expires_at,
        }

    with pg_conn() as conn:
        with _cursor(conn, cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, subject, role, projects, active, expires_at
                FROM api_tokens
                WHERE token_hash = %s
                """,
                (token_hash,),
            )
            row = cur.fetchone()

    if not row or not row.get("active"):
        return None

    expires_at = row.get("expires_at")
    if expires_at is not None and expires_at <= datetime.utcnow():
        return None

    projects = row.get("projects") or []
    if isinstance(projects, str):
        try:
            projects = json.loads(projects)
        except json.JSONDecodeError:
            projects = []

    return {
        "token_id": str(row["id"]),
        "subject": row["subject"],
        "role": row["role"],
        "projects": projects,
        "expires_at": expires_at,
    }


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


def enqueue_job(spec: JobSpec, submitted_by: str | None = None) -> tuple[JobStatus, bool]:
    if not spec.job_id:
        raise ValueError("job_id is required")
    if not spec.project.strip():
        raise ValueError("project is required")

    serialized_spec = spec.model_dump()
    serialized_spec["project"] = spec.project
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
                project=spec.project,
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
                INSERT INTO jobs (job_id, project, submitted_by, spec, status, timestamps)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s::jsonb)
                ON CONFLICT (job_id) DO NOTHING
                RETURNING status, node_id, gpu_ids, timestamps, exit_code, reason, project
                """,
                (
                    spec.job_id,
                    spec.project,
                    submitted_by,
                    json.dumps(serialized_spec),
                    JobState.QUEUED.value,
                    json.dumps({"enqueued": enqueued_ts}),
                ),
            )
            row = cur.fetchone()

            if row is None:
                cur.execute(
                    """
                    SELECT status, node_id, gpu_ids, timestamps, exit_code, reason, project
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
            project=spec.project,
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
                "SELECT spec, status, node_id, gpu_ids, timestamps, exit_code, reason FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
        return _to_sqlite_job_status(row) if row else None

    with pg_conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "SELECT status, node_id, gpu_ids, timestamps, exit_code, reason, project FROM jobs WHERE job_id = %s",
                (job_id,),
            )
            row = cur.fetchone()

    if not row:
        return None

    return _job_status_from_row(row)


def get_job_project(job_id: str) -> Optional[str]:
    if _use_sqlite():
        with _sqlite_conn() as conn:
            row = conn.execute("SELECT spec FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not row:
            return None
        spec_payload = _json_load(row["spec"], {})
        project = spec_payload.get("project")
        return str(project) if project is not None else "default"

    with pg_conn() as conn:
        with _cursor(conn) as cur:
            cur.execute("SELECT project FROM jobs WHERE job_id = %s", (job_id,))
            row = cur.fetchone()
    if not row:
        return None
    if isinstance(row, dict):
        return row.get("project")
    return row[0]


def get_job_spec(job_id: str) -> Optional[JobSpec]:
    r = redis_client()
    spec_raw = r.get(f"{_SPEC_KEY_PREFIX}{job_id}")
    if spec_raw:
        payload = json.loads(spec_raw)
        payload.setdefault("project", "default")
        return JobSpec.model_validate(payload)

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
    spec_payload.setdefault("project", "default")
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
    if not node.node_id:
        raise ValueError("node_id is required")
    serialized_gpus = json.dumps([gpu.model_dump() for gpu in node.gpus])
    labels_json = json.dumps(node.labels or {})
    agent_health_json = json.dumps(node.agent_health or {})

    if _use_sqlite():
        with _sqlite_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO nodes (node_id, labels, gpus, agent_health, last_seen)
                VALUES (?, ?, ?, ?, ?)
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


def _project_access_clause(is_admin: bool) -> str:
    if is_admin:
        return ""
    return "WHERE project = ANY(%s)"


def _project_allowed(project: str, is_admin: bool, projects: Optional[List[str]]) -> bool:
    if is_admin or projects is None:
        return True
    return project in projects


def list_jobs(is_admin: bool = False, projects: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """
    Fetch all jobs ordered by enqueue time descending.
    Returns flat dicts with {job_id, state, node_id, gpu_ids, timestamps, exit_code, reason}.
    """
    if not is_admin and projects is not None and not projects:
        return []

    if _use_sqlite():
        with _sqlite_conn() as conn:
            rows = conn.execute(
                """
                SELECT job_id, spec, status, backend_ref, node_id, gpu_ids, timestamps, exit_code, reason
                FROM jobs
                """
            ).fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            spec_payload = _json_load(row["spec"], {})
            project = str(spec_payload.get("project", "default"))
            if not _project_allowed(project, is_admin, projects):
                continue
            timestamps = _json_load(row["timestamps"], {})
            result.append(
                {
                    "job_id": row["job_id"],
                    "project": project,
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

    unscoped = projects is None
    clause = _project_access_clause(is_admin) if not unscoped else ""
    params: tuple[Any, ...] = () if (is_admin or unscoped) else (projects,)
    with pg_conn() as conn:
        with _cursor(conn, cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                f"""
                SELECT job_id, project, status, backend_ref, node_id, gpu_ids, timestamps, exit_code, reason
                FROM jobs
                {clause}
                ORDER BY (timestamps->>'enqueued')::float DESC NULLS LAST
                """,
                params,
            )
            rows = cur.fetchall()

    result: List[Dict[str, Any]] = []
    for row in rows:
        result.append(
            {
                "job_id": row["job_id"],
                "project": row.get("project") or "default",
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


def job_summary(is_admin: bool = False, projects: Optional[List[str]] = None) -> Dict[str, int]:
    """
    Return aggregate job counts by state for the dashboard.
    """
    if not is_admin and projects is not None and not projects:
        return _empty_job_counts()

    if _use_sqlite():
        with _sqlite_conn() as conn:
            rows = conn.execute("SELECT status, spec FROM jobs").fetchall()
        counts = _empty_job_counts()
        for row in rows:
            spec_payload = _json_load(row["spec"], {})
            project = str(spec_payload.get("project", "default"))
            if not _project_allowed(project, is_admin, projects):
                continue
            key = str(row["status"]).lower()
            if key in counts:
                counts[key] += 1
        return counts

    if is_admin or projects is None:
        sql = "SELECT status, COUNT(*) AS cnt FROM jobs GROUP BY status"
        params: tuple[Any, ...] = ()
    else:
        sql = "SELECT status, COUNT(*) AS cnt FROM jobs WHERE project = ANY(%s) GROUP BY status"
        params = (projects,)

    with pg_conn() as conn:
        with _cursor(conn, cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
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
                INSERT OR REPLACE INTO scheduler_settings (singleton_key, active_policy, updated_at, updated_by)
                VALUES (?, ?, ?, ?)
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
                INSERT OR REPLACE INTO scheduler_settings (singleton_key, active_policy, updated_at, updated_by)
                VALUES (?, ?, ?, ?)
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


def read_metrics_summary(
    window_minutes: int,
    fresh_node_seconds: int,
    is_admin: bool = False,
    projects: Optional[List[str]] = None,
) -> Dict[str, Any]:
    window_start = time.time() - (window_minutes * 60)
    queue_depth = int(redis_client().llen(_QUEUE_KEY))
    current_counts = job_summary(is_admin=is_admin, projects=projects)

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
                SELECT status, timestamps, spec
                FROM jobs
                WHERE timestamps IS NOT NULL
                """
            ).fetchall()
        node_row = node_row or {"total": 0, "fresh": 0}

        job_rows: List[Dict[str, Any]] = []
        for row in raw_job_rows:
            timestamps = _json_load(row["timestamps"], {})
            spec_payload = _json_load(row["spec"], {})
            project = str(spec_payload.get("project", "default"))
            if not _project_allowed(project, is_admin, projects):
                continue
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

                if not is_admin and projects is not None and not projects:
                    job_rows = []
                else:
                    params: List[Any] = [
                        window_start,
                        window_start,
                        window_start,
                        window_start,
                        window_start,
                        window_start,
                    ]
                    project_clause = ""
                    if not is_admin and projects is not None:
                        project_clause = "AND project = ANY(%s)"
                        params.append(projects)

                    cur.execute(
                        f"""
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
                          {project_clause}
                        """,
                        tuple(params),
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


def create_token_request(
    subject_name: str,
    email: str,
    requested_projects: List[str],
    purpose: str,
) -> Dict[str, Any]:
    request_id = str(uuid.uuid4())
    if _use_sqlite():
        created_at = _utcnow_iso()
        with _sqlite_conn() as conn:
            conn.execute(
                """
                INSERT INTO token_requests (id, subject_name, email, requested_projects, purpose, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (request_id, subject_name, email, json.dumps(requested_projects), purpose, "PENDING", created_at),
            )
            conn.execute(
                "INSERT INTO events (ts, job_id, kind, payload) VALUES (?, ?, ?, ?)",
                (time.time(), None, "token_request_created", json.dumps({"request_id": request_id, "email": email})),
            )
        return {
            "request_id": request_id,
            "status": "PENDING",
            "subject_name": subject_name,
            "email": email,
            "requested_projects": requested_projects,
            "purpose": purpose,
        }

    with pg_conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                """
                INSERT INTO token_requests (id, subject_name, email, requested_projects, purpose, status)
                VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                """,
                (request_id, subject_name, email, json.dumps(requested_projects), purpose, "PENDING"),
            )
            cur.execute(
                "INSERT INTO events (job_id, kind, payload) VALUES (%s, %s, %s::jsonb)",
                (None, "token_request_created", json.dumps({"request_id": request_id, "email": email})),
            )
    return {
        "request_id": request_id,
        "status": "PENDING",
        "subject_name": subject_name,
        "email": email,
        "requested_projects": requested_projects,
        "purpose": purpose,
    }


def list_token_requests(status: Optional[str] = None) -> List[Dict[str, Any]]:
    if _use_sqlite():
        with _sqlite_conn() as conn:
            if status:
                rows = conn.execute(
                    """
                    SELECT id, subject_name, email, requested_projects, purpose, status,
                           review_notes, reviewed_by, created_at, reviewed_at
                    FROM token_requests
                    WHERE status = ?
                    ORDER BY created_at DESC
                    """,
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, subject_name, email, requested_projects, purpose, status,
                           review_notes, reviewed_by, created_at, reviewed_at
                    FROM token_requests
                    ORDER BY created_at DESC
                    """
                ).fetchall()

        result: List[Dict[str, Any]] = []
        for row in rows:
            result.append(
                {
                    "request_id": str(row["id"]),
                    "subject_name": row["subject_name"],
                    "email": row["email"],
                    "requested_projects": _json_load(row["requested_projects"], []),
                    "purpose": row["purpose"],
                    "status": row["status"],
                    "review_notes": row["review_notes"],
                    "reviewed_by": row["reviewed_by"],
                    "created_at": _iso_or_none(row["created_at"]),
                    "reviewed_at": _iso_or_none(row["reviewed_at"]),
                }
            )
        return result

    with pg_conn() as conn:
        with _cursor(conn, cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if status:
                cur.execute(
                    """
                    SELECT id, subject_name, email, requested_projects, purpose, status,
                           review_notes, reviewed_by, created_at, reviewed_at
                    FROM token_requests
                    WHERE status = %s
                    ORDER BY created_at DESC
                    """,
                    (status,),
                )
            else:
                cur.execute(
                    """
                    SELECT id, subject_name, email, requested_projects, purpose, status,
                           review_notes, reviewed_by, created_at, reviewed_at
                    FROM token_requests
                    ORDER BY created_at DESC
                    """
                )
            rows = cur.fetchall()

    result: List[Dict[str, Any]] = []
    for row in rows:
        projects = row["requested_projects"] or []
        if isinstance(projects, str):
            projects = json.loads(projects)
        result.append(
            {
                "request_id": str(row["id"]),
                "subject_name": row["subject_name"],
                "email": row["email"],
                "requested_projects": projects,
                "purpose": row["purpose"],
                "status": row["status"],
                "review_notes": row["review_notes"],
                "reviewed_by": row["reviewed_by"],
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "reviewed_at": row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
            }
        )
    return result


def approve_token_request(
    request_id: str,
    reviewed_by: str,
    deliver: Callable[[str, str, str], None],
    review_notes: Optional[str] = None,
    role: str = "user",
    ttl_days: int = 90,
    delivery_mode: str = "email",
) -> Dict[str, Any]:
    if role not in {"user", "admin"}:
        raise ValueError(f"Unsupported role: {role}")
    if delivery_mode not in {"email", "response"}:
        raise ValueError(f"Unsupported token delivery mode: {delivery_mode}")

    if _use_sqlite():
        with _sqlite_conn() as conn:
            req = conn.execute(
                """
                SELECT id, subject_name, email, requested_projects, status
                FROM token_requests
                WHERE id = ?
                """,
                (request_id,),
            ).fetchone()
            if not req:
                raise KeyError(f"Token request {request_id} not found")
            if req["status"] != "PENDING":
                raise ValueError(f"Token request {request_id} is already {req['status']}")

            plaintext_token = generate_token()
            token_id = str(uuid.uuid4())
            projects = _json_load(req["requested_projects"], [])
            expires_at = datetime.utcnow() + timedelta(days=ttl_days)
            reviewed_at = _utcnow_iso()

            conn.execute(
                """
                INSERT INTO api_tokens
                (id, token_hash, subject, role, projects, active, expires_at, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    token_id,
                    hash_token(plaintext_token),
                    req["subject_name"],
                    role,
                    json.dumps(projects),
                    expires_at.isoformat(),
                    reviewed_at,
                    reviewed_by,
                ),
            )
            conn.execute(
                """
                UPDATE token_requests
                SET status = ?, review_notes = ?, reviewed_by = ?, reviewed_at = ?
                WHERE id = ?
                """,
                ("APPROVED", review_notes, reviewed_by, reviewed_at, request_id),
            )
            conn.execute(
                "INSERT INTO events (ts, job_id, kind, payload) VALUES (?, ?, ?, ?)",
                (
                    time.time(),
                    None,
                    "token_request_approved",
                    json.dumps(
                        {
                            "request_id": request_id,
                            "token_id": token_id,
                            "reviewed_by": reviewed_by,
                        }
                    ),
                ),
            )
            if delivery_mode == "email":
                deliver(req["email"], req["subject_name"], plaintext_token)

        result = {
            "request_id": request_id,
            "status": "APPROVED",
            "token_id": token_id,
            "expires_at": expires_at.isoformat(),
        }
        if delivery_mode == "response":
            result["plaintext_token"] = plaintext_token
        return result

    conn = pg_conn()
    conn.autocommit = False
    try:
        with _cursor(conn, cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, subject_name, email, requested_projects, status
                FROM token_requests
                WHERE id = %s
                FOR UPDATE
                """,
                (request_id,),
            )
            req = cur.fetchone()
            if not req:
                raise KeyError(f"Token request {request_id} not found")
            if req["status"] != "PENDING":
                raise ValueError(f"Token request {request_id} is already {req['status']}")

            plaintext_token = generate_token()
            token_id = str(uuid.uuid4())
            projects = req["requested_projects"] or []
            if isinstance(projects, str):
                projects = json.loads(projects)
            expires_at = datetime.utcnow() + timedelta(days=ttl_days)

            cur.execute(
                """
                INSERT INTO api_tokens (id, token_hash, subject, role, projects, active, expires_at, created_by)
                VALUES (%s, %s, %s, %s, %s::jsonb, TRUE, %s, %s)
                """,
                (
                    token_id,
                    hash_token(plaintext_token),
                    req["subject_name"],
                    role,
                    json.dumps(projects),
                    expires_at,
                    reviewed_by,
                ),
            )
            cur.execute(
                """
                UPDATE token_requests
                SET status='APPROVED',
                    review_notes=%s,
                    reviewed_by=%s,
                    reviewed_at=NOW()
                WHERE id=%s
                """,
                (review_notes, reviewed_by, request_id),
            )
            cur.execute(
                "INSERT INTO events (job_id, kind, payload) VALUES (%s, %s, %s::jsonb)",
                (
                    None,
                    "token_request_approved",
                    json.dumps(
                        {
                            "request_id": request_id,
                            "token_id": token_id,
                            "reviewed_by": reviewed_by,
                        }
                    ),
                ),
            )

        if delivery_mode == "email":
            deliver(req["email"], req["subject_name"], plaintext_token)
        conn.commit()
        result = {
            "request_id": request_id,
            "status": "APPROVED",
            "token_id": token_id,
            "expires_at": expires_at.isoformat(),
        }
        if delivery_mode == "response":
            result["plaintext_token"] = plaintext_token
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reject_token_request(
    request_id: str,
    reviewed_by: str,
    review_notes: Optional[str] = None,
) -> Dict[str, Any]:
    if _use_sqlite():
        reviewed_at = _utcnow_iso()
        with _sqlite_conn() as conn:
            row = conn.execute("SELECT status FROM token_requests WHERE id = ?", (request_id,)).fetchone()
            if row is None:
                raise KeyError(f"Token request {request_id} not found")
            if row["status"] != "PENDING":
                raise ValueError(f"Token request {request_id} is already {row['status']}")

            conn.execute(
                """
                UPDATE token_requests
                SET status = ?, review_notes = ?, reviewed_by = ?, reviewed_at = ?
                WHERE id = ?
                """,
                ("REJECTED", review_notes, reviewed_by, reviewed_at, request_id),
            )
            conn.execute(
                "INSERT INTO events (ts, job_id, kind, payload) VALUES (?, ?, ?, ?)",
                (
                    time.time(),
                    None,
                    "token_request_rejected",
                    json.dumps({"request_id": request_id, "reviewed_by": reviewed_by}),
                ),
            )

        return {
            "request_id": request_id,
            "status": "REJECTED",
        }

    with pg_conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                """
                UPDATE token_requests
                SET status='REJECTED',
                    review_notes=%s,
                    reviewed_by=%s,
                    reviewed_at=NOW()
                WHERE id=%s AND status='PENDING'
                """,
                (review_notes, reviewed_by, request_id),
            )
            if cur.rowcount == 0:
                cur.execute("SELECT status FROM token_requests WHERE id=%s", (request_id,))
                row = cur.fetchone()
                if not row:
                    raise KeyError(f"Token request {request_id} not found")
                existing_status = row[0] if not isinstance(row, dict) else row.get("status")
                raise ValueError(f"Token request {request_id} is already {existing_status}")

            cur.execute(
                "INSERT INTO events (job_id, kind, payload) VALUES (%s, %s, %s::jsonb)",
                (
                    None,
                    "token_request_rejected",
                    json.dumps({"request_id": request_id, "reviewed_by": reviewed_by}),
                ),
            )

    return {
        "request_id": request_id,
        "status": "REJECTED",
    }


def list_api_tokens() -> List[Dict[str, Any]]:
    if _use_sqlite():
        with _sqlite_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, subject, role, projects, active, expires_at, created_at, created_by
                FROM api_tokens
                ORDER BY created_at DESC
                """
            ).fetchall()

        tokens: List[Dict[str, Any]] = []
        for row in rows:
            tokens.append(
                {
                    "token_id": str(row["id"]),
                    "subject": row["subject"],
                    "role": row["role"],
                    "projects": _json_load(row["projects"], []),
                    "active": bool(row["active"]),
                    "expires_at": _iso_or_none(row["expires_at"]),
                    "created_at": _iso_or_none(row["created_at"]),
                    "created_by": row["created_by"],
                }
            )
        return tokens

    with pg_conn() as conn:
        with _cursor(conn, cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, subject, role, projects, active, expires_at, created_at, created_by
                FROM api_tokens
                ORDER BY created_at DESC
                """
            )
            rows = cur.fetchall()

    tokens: List[Dict[str, Any]] = []
    for row in rows:
        projects = row["projects"] or []
        if isinstance(projects, str):
            projects = json.loads(projects)
        tokens.append(
            {
                "token_id": str(row["id"]),
                "subject": row["subject"],
                "role": row["role"],
                "projects": projects,
                "active": bool(row["active"]),
                "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "created_by": row["created_by"],
            }
        )
    return tokens


def revoke_api_token(token_id: str, revoked_by: str) -> Dict[str, Any]:
    if _use_sqlite():
        with _sqlite_conn() as conn:
            cur = conn.execute("UPDATE api_tokens SET active = 0 WHERE id = ?", (token_id,))
            if cur.rowcount == 0:
                raise KeyError(f"Token {token_id} not found")
            conn.execute(
                "INSERT INTO events (ts, job_id, kind, payload) VALUES (?, ?, ?, ?)",
                (
                    time.time(),
                    None,
                    "token_revoked",
                    json.dumps({"token_id": token_id, "revoked_by": revoked_by}),
                ),
            )
        return {"token_id": token_id, "revoked": True}

    with pg_conn() as conn:
        with _cursor(conn) as cur:
            cur.execute(
                "UPDATE api_tokens SET active=FALSE WHERE id=%s::uuid",
                (token_id,),
            )
            if cur.rowcount == 0:
                raise KeyError(f"Token {token_id} not found")

            cur.execute(
                "INSERT INTO events (job_id, kind, payload) VALUES (%s, %s, %s::jsonb)",
                (
                    None,
                    "token_revoked",
                    json.dumps({"token_id": token_id, "revoked_by": revoked_by}),
                ),
            )

    return {"token_id": token_id, "revoked": True}


def supported_policies() -> List[str]:
    return _supported_policy_values()
