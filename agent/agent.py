import logging
import os
import platform
import shutil
import subprocess
import threading
from time import sleep, time
from typing import Optional

import requests
from fastapi import FastAPI
from pydantic import BaseModel

from .loggingConf import configure_logging  # <= NOTE: CamelCase file

configure_logging()
logger = logging.getLogger("agent")

APP_VERSION = os.getenv("APP_VERSION", "0.1.0-m1")
NODE_ID = os.getenv("NODE_ID", os.uname().nodename)
CONTROL_PLANE_API = os.getenv("CONTROL_PLANE_API", "http://control-plane:8000")
HEARTBEAT_INTERVAL = float(os.getenv("HEARTBEAT_INTERVAL", "5"))
FAKE_GPU_COUNT = int(os.getenv("FAKE_GPU_COUNT", "2"))
FAKE_GPU_MEM_MB = int(os.getenv("FAKE_GPU_MEM_MB", "24576"))
ASSIGNMENT_POLL_INTERVAL = float(os.getenv("ASSIGNMENT_POLL_INTERVAL", "2"))
EXECUTOR_MODE = os.getenv("EXECUTOR_MODE", "auto").lower()
GPU_CAPACITY = int(os.getenv("GPU_CAPACITY", str(FAKE_GPU_COUNT)))
SIMULATED_RUN_SECONDS = float(os.getenv("SIMULATED_RUN_SECONDS", "3"))

_REAL_GPU_AVAILABLE: Optional[bool] = None
_DETECTED_GPU_COUNT: Optional[int] = None

app = FastAPI(
    title=f"CUDA Overlay Agent ({NODE_ID})",
    version=APP_VERSION,
    description="Agent process for executing tasks on a node (Milestone 1: no-op run).",
)


class RunReq(BaseModel):
    job_id: str
    image: str
    cmd: list[str]
    env: dict[str, str] = {}
    gpu_id: int = 0


def _detect_real_gpu() -> bool:
    if EXECUTOR_MODE == "simulate":
        return False
    if EXECUTOR_MODE == "force":
        return True
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return False
    try:
        result = subprocess.run(
            [nvidia_smi, "-L"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    lines = [line for line in (result.stdout or "").splitlines() if line.strip()]
    if lines:
        global _DETECTED_GPU_COUNT  # noqa: PLW0603
        _DETECTED_GPU_COUNT = len(lines)
    return True


def _has_real_gpu() -> bool:
    global _REAL_GPU_AVAILABLE  # noqa: PLW0603
    if _REAL_GPU_AVAILABLE is None:
        _REAL_GPU_AVAILABLE = _detect_real_gpu()
    return bool(_REAL_GPU_AVAILABLE)


def _reported_gpu_count() -> int:
    if _has_real_gpu():
        if _DETECTED_GPU_COUNT:
            return _DETECTED_GPU_COUNT
        return max(GPU_CAPACITY, 1)
    return FAKE_GPU_COUNT


def _fake_gpu_inventory() -> list[dict]:
    """
    Generate deterministic-but-changing fake GPU metrics so UIs have data.
    """
    stamp = time()
    gpus = []
    for idx in range(_reported_gpu_count()):
        utilization = ((stamp / (idx + 1)) % 1.0) * 100.0
        mem_used = int((stamp * (idx + 2)) % FAKE_GPU_MEM_MB)
        temperature = int(30 + ((stamp + idx * 3) % 40))
        gpus.append(
            {
                "index": idx,
                "name": f"FakeGPU-{idx}",
                "mem_total_mb": FAKE_GPU_MEM_MB,
                "utilization": round(utilization, 2),
                "mem_used_mb": mem_used,
                "temperature": temperature,
            }
        )
    return gpus


def _heartbeat_payload() -> dict:
    return {
        "node_id": NODE_ID,
        "labels": {
            "arch": platform.machine(),
            "os": platform.system(),
            "app_version": APP_VERSION,
            "has_real_gpu": str(_has_real_gpu()).lower(),
        },
        "gpus": _fake_gpu_inventory(),
        "agent_health": {"heartbeat_ts": time()},
    }


def _send_heartbeat():
    try:
        resp = requests.post(
            f"{CONTROL_PLANE_API}/api/nodes",
            json=_heartbeat_payload(),
            timeout=5,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Heartbeat rejected (%s): %s", resp.status_code, resp.text[:200]
            )
    except Exception as exc:
        logger.warning("Heartbeat failed: %s", exc)


_hb_stop_event = threading.Event()
_hb_thread: Optional[threading.Thread] = None
_worker_stop_event = threading.Event()
_worker_thread: Optional[threading.Thread] = None


def _heartbeat_loop():
    logger.info(
        "Starting heartbeat loop to %s every %ss", CONTROL_PLANE_API, HEARTBEAT_INTERVAL
    )
    while not _hb_stop_event.is_set():
        _send_heartbeat()
        _hb_stop_event.wait(HEARTBEAT_INTERVAL)


def _assignment_loop():
    logger.info(
        "Starting assignment poll loop against %s (interval=%ss)",
        CONTROL_PLANE_API,
        ASSIGNMENT_POLL_INTERVAL,
    )
    while not _worker_stop_event.is_set():
        try:
            resp = requests.post(
                f"{CONTROL_PLANE_API}/api/nodes/{NODE_ID}/assignments/next",
                timeout=HEARTBEAT_INTERVAL,
            )
        except Exception as exc:
            logger.warning("Assignment poll failed: %s", exc)
            _worker_stop_event.wait(ASSIGNMENT_POLL_INTERVAL)
            continue

        if resp.status_code == 204:
            _worker_stop_event.wait(ASSIGNMENT_POLL_INTERVAL)
            continue
        if resp.status_code >= 400:
            logger.warning(
                "Assignment request returned %s: %s",
                resp.status_code,
                resp.text[:200],
            )
            _worker_stop_event.wait(ASSIGNMENT_POLL_INTERVAL)
            continue

        try:
            payload = resp.json()
        except ValueError:
            logger.warning("Invalid JSON from assignment endpoint: %s", resp.text[:200])
            _worker_stop_event.wait(ASSIGNMENT_POLL_INTERVAL)
            continue
        _process_assignment(payload)


def _process_assignment(payload: dict):
    job_id = payload.get("job_id")
    spec = payload.get("spec") or {}
    if not job_id:
        logger.warning("Assignment payload missing job_id: %s", payload)
        return
    gpu_ids = _allocate_gpu_ids(spec)
    logger.info("Claimed job %s with spec %s", job_id, spec)
    _notify_job_start(job_id, gpu_ids)
    exit_code, reason = _execute_job(job_id, spec, gpu_ids)
    success = exit_code == 0
    _notify_job_finish(job_id, success, exit_code, reason)


def _allocate_gpu_ids(spec: dict) -> list[int]:
    requested = spec.get("gpus", 1)
    try:
        requested = int(requested)
    except (TypeError, ValueError):
        requested = 1
    requested = max(1, requested)
    capacity = _reported_gpu_count()
    return list(range(min(requested, capacity)))


def _execute_job(job_id: str, spec: dict, gpu_ids: list[int]) -> tuple[int, Optional[str]]:
    cmd = spec.get("cmd") or []
    env = os.environ.copy()
    env.update(spec.get("env") or {})
    metadata = spec.get("metadata") or {}
    if _has_real_gpu() and gpu_ids:
        env.setdefault("CUDA_VISIBLE_DEVICES", ",".join(str(idx) for idx in gpu_ids))

    if not cmd:
        logger.warning("Job %s missing command; failing", job_id)
        return 1, "No command provided"

    if not _has_real_gpu():
        duration = metadata.get("sim_seconds")
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = SIMULATED_RUN_SECONDS
        duration = max(0.5, duration)
        logger.info("Simulating job %s for %ss (%s)", job_id, duration, cmd)
        sleep(duration)
        return 0, None

    logger.info("Executing job %s locally (cmd=%s, image=%s)", job_id, cmd, spec.get("image"))
    try:
        proc = subprocess.run(
            cmd,
            env=env,
            check=False,
        )
        exit_code = proc.returncode
        reason = None if exit_code == 0 else f"Process exited with code {exit_code}"
        return exit_code, reason
    except FileNotFoundError as exc:
        logger.error("Command not found for job %s: %s", job_id, exc)
        return 1, f"Command not found: {exc}"
    except Exception as exc:
        logger.error("Execution failed for job %s: %s", job_id, exc)
        return 1, f"Execution failed: {exc}"


def _notify_job_start(job_id: str, gpu_ids: list[int]):
    payload = {"node_id": NODE_ID, "gpu_ids": gpu_ids}
    try:
        resp = requests.post(
            f"{CONTROL_PLANE_API}/api/jobs/{job_id}/start",
            json=payload,
            timeout=10,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Failed to ack start of %s (%s): %s",
                job_id,
                resp.status_code,
                resp.text[:200],
            )
    except Exception as exc:
        logger.warning("Failed to notify start for job %s: %s", job_id, exc)


def _notify_job_finish(job_id: str, success: bool, exit_code: int, reason: Optional[str]):
    payload = {
        "node_id": NODE_ID,
        "success": success,
        "exit_code": exit_code,
        "reason": reason,
    }
    try:
        resp = requests.post(
            f"{CONTROL_PLANE_API}/api/jobs/{job_id}/finish",
            json=payload,
            timeout=10,
        )
        if resp.status_code >= 400:
            logger.warning(
                "Failed to finish job %s (%s): %s",
                job_id,
                resp.status_code,
                resp.text[:200],
            )
    except Exception as exc:
        logger.warning("Failed to notify completion for job %s: %s", job_id, exc)


@app.on_event("startup")
def _start_background_tasks():
    global _hb_thread, _worker_thread  # noqa: PLW0603
    _hb_stop_event.clear()
    if _hb_thread is None or not _hb_thread.is_alive():
        _hb_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        _hb_thread.start()
    _worker_stop_event.clear()
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_thread = threading.Thread(target=_assignment_loop, daemon=True)
        _worker_thread.start()


@app.on_event("shutdown")
def _stop_background_tasks():
    _hb_stop_event.set()
    _worker_stop_event.set()
    if _hb_thread is not None and _hb_thread.is_alive():
        _hb_thread.join(timeout=1)
    if _worker_thread is not None and _worker_thread.is_alive():
        _worker_thread.join(timeout=1)


@app.get("/health")
def health():
    return {
        "ok": True,
        "node_id": NODE_ID,
        "service": "agent",
        "control_plane": CONTROL_PLANE_API,
    }


@app.post("/run")
def run(req: RunReq):
    # No-op execution for M1; proves API plumbing only
    logger.info(f"ACCEPT job {req.job_id} on gpu {req.gpu_id}: {req.image} {req.cmd}")
    return {"accepted": True, "node_id": NODE_ID, "gpu_id": req.gpu_id, "ts": time()}
