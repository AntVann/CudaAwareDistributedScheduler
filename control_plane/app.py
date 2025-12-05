import logging
import os
import threading
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from control_plane.api.admin import router as admin_router
from control_plane.api.jobs import router as jobs_router
from control_plane.api.nodes import router as nodes_router
from control_plane.api.policies import router as policies_router
from control_plane.loggingConf import configure_logging
from control_plane.core.scheduler import NaiveScheduler
from control_plane.core.persistence import (
    bootstrap_storage,
    check_postgres,
    check_redis,
    ready_report,
)

configure_logging()
logger = logging.getLogger("control_plane")

APP_VERSION = os.getenv("APP_VERSION", "0.4.0-m4")
scheduler = NaiveScheduler(loop_secs=1)

app = FastAPI(
    title="CUDA Overlay Control Plane",
    version=APP_VERSION,
    description="Control plane service for the CUDA-aware scheduler (Milestone 1 baseline + Milestone 2 scaffold).",
)

# Dev CORS (lock down later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    logger.info("Bootstrapping storage (schema + readiness checks)...")
    bootstrap_storage()
    ok_pg = check_postgres()
    ok_redis = check_redis()
    if not (ok_pg and ok_redis):
        logger.error("Storage bootstrap failed: postgres=%s redis=%s", ok_pg, ok_redis)
    else:
        logger.info("Storage bootstrap OK: postgres=%s redis=%s", ok_pg, ok_redis)
    _start_scheduler_loop()

@app.get("/health")
def health():
    return {"ok": True, "service": "control_plane"}

@app.get("/version")
def version():
    return {"version": APP_VERSION}

@app.get("/ready")
def ready():
    """
    Readiness probe:
    - 200 when Postgres & Redis are reachable
    - 503 otherwise
    """
    report = ready_report()
    status = 200 if report.get("ok") else 503
    return JSONResponse(report, status_code=status)

@app.get("/")
def root():
    return JSONResponse({"service": "control_plane", "version": APP_VERSION})

def _start_scheduler_loop():
    """
    Background scheduler loop that pops from the global queue and places onto per-node queues.
    """
    def run_loop():
        while True:
            try:
                scheduler.tick()
            except Exception:
                logger.exception("Scheduler tick failed")
            time.sleep(scheduler.loop_secs)

    threading.Thread(target=run_loop, daemon=True).start()

app.include_router(policies_router, prefix="/api")
app.include_router(nodes_router, prefix="/api")
app.include_router(jobs_router, prefix="/api")
app.include_router(admin_router, prefix="/api")
