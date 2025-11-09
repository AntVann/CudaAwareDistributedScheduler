from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
import logging

from .loggingConf import configure_logging
from .core.persistence import ready_report

# Initialize logging first
configure_logging()
logger = logging.getLogger("control_plane")

APP_VERSION = os.getenv("APP_VERSION", "0.1.0-m1")

app = FastAPI(
    title="CUDA Overlay Control Plane",
    version=APP_VERSION,
    description="Control plane for user-space CUDA-aware overlay scheduler (Milestone 1 + /ready).",
)

# Dev CORS (lock down later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
