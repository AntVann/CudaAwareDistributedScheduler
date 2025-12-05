import logging
import os
import platform
import threading
from time import time
from typing import Optional

import requests
from fastapi import FastAPI
from pydantic import BaseModel

from .loggingConf import configure_logging  # <= NOTE: CamelCase file
from agent.worker import loop as worker_loop

configure_logging()
logger = logging.getLogger("agent")

APP_VERSION = os.getenv("APP_VERSION", "0.1.0-m1")
NODE_ID = os.getenv("NODE_ID", os.uname().nodename)
CONTROL_PLANE_API = os.getenv("CONTROL_PLANE_API", "http://control-plane:8000")
HEARTBEAT_INTERVAL = float(os.getenv("HEARTBEAT_INTERVAL", "5"))
FAKE_GPU_COUNT = int(os.getenv("FAKE_GPU_COUNT", "2"))
FAKE_GPU_MEM_MB = int(os.getenv("FAKE_GPU_MEM_MB", "24576"))

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


def _fake_gpu_inventory() -> list[dict]:
    """
    Generate deterministic-but-changing fake GPU metrics so UIs have data.
    """
    stamp = time()
    gpus = []
    for idx in range(FAKE_GPU_COUNT):
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


_stop_event = threading.Event()
_hb_thread: Optional[threading.Thread] = None
_worker_thread: Optional[threading.Thread] = None


def _heartbeat_loop():
    logger.info(
        "Starting heartbeat loop to %s every %ss", CONTROL_PLANE_API, HEARTBEAT_INTERVAL
    )
    while not _stop_event.is_set():
        _send_heartbeat()
        _stop_event.wait(HEARTBEAT_INTERVAL)


@app.on_event("startup")
def _start_background_tasks():
    global _hb_thread  # noqa: PLW0603
    _stop_event.clear()
    if _hb_thread is None or not _hb_thread.is_alive():
        _hb_thread = threading.Thread(target=_heartbeat_loop, daemon=True)
        _hb_thread.start()
    global _worker_thread  # noqa: PLW0603
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_thread = threading.Thread(target=worker_loop, daemon=True)
        _worker_thread.start()


@app.on_event("shutdown")
def _stop_background_tasks():
    _stop_event.set()
    if _hb_thread is not None and _hb_thread.is_alive():
        _hb_thread.join(timeout=1)


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
