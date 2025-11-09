import os
import logging
import psycopg2
import psycopg2.extras
import redis
from typing import Tuple, Dict, Any

logger = logging.getLogger("control_plane.persistence")

# ---------- Connection factories ----------

def get_pg_conn():
    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        dbname=os.getenv("POSTGRES_DB", "overlay"),
        user=os.getenv("POSTGRES_USER", "overlay"),
        password=os.getenv("POSTGRES_PASSWORD", "overlay"),
    )
    # Autocommit for simple ops; we’ll pool later
    conn.autocommit = True
    return conn

def get_redis():
    r = redis.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=int(os.getenv("REDIS_DB", "0")),
        decode_responses=True,
    )
    return r

# ---------- Readiness checks (no writes) ----------

def check_postgres_ready() -> Tuple[bool, Dict[str, Any]]:
    """
    Non-invasive readiness check:
    - open connection
    - SELECT 1
    - return basic server info
    """
    conn = None
    try:
        conn = get_pg_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT 1;")
            _ = cur.fetchone()
        info = {
            "host": os.getenv("POSTGRES_HOST", "postgres"),
            "port": int(os.getenv("POSTGRES_PORT", "5432")),
            "db": os.getenv("POSTGRES_DB", "overlay"),
            "server_version": getattr(conn, "server_version", None),
        }
        return True, info
    except Exception as e:
        logger.exception("Postgres readiness check failed")
        return False, {"error": str(e)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

def check_redis_ready() -> Tuple[bool, Dict[str, Any]]:
    """
    Non-invasive readiness check:
    - ping
    - return basic server info (lightweight)
    """
    try:
        r = get_redis()
        pong = r.ping()
        # Keep it light; avoid dumping full INFO
        info = {
            "host": os.getenv("REDIS_HOST", "redis"),
            "port": int(os.getenv("REDIS_PORT", "6379")),
            "pong": bool(pong),
        }
        return True, info
    except Exception as e:
        logger.exception("Redis readiness check failed")
        return False, {"error": str(e)}

def ready_report() -> Dict[str, Any]:
    ok_pg, pg_info = check_postgres_ready()
    ok_redis, redis_info = check_redis_ready()
    ok = ok_pg and ok_redis
    return {
        "ok": ok,
        "postgres": {"ok": ok_pg, **pg_info},
        "redis": {"ok": ok_redis, **redis_info},
    }
