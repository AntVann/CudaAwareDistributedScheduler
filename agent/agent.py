from fastapi import FastAPI
from pydantic import BaseModel
import os
import logging
from .loggingConf import configure_logging   # <= NOTE: CamelCase file
from time import time

configure_logging()
logger = logging.getLogger("agent")

APP_VERSION = os.getenv("APP_VERSION", "0.1.0-m1")
NODE_ID = os.getenv("NODE_ID", os.uname().nodename)

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

@app.get("/health")
def health():
    return {"ok": True, "node_id": NODE_ID, "service": "agent"}

@app.post("/run")
def run(req: RunReq):
    # No-op execution for M1; proves API plumbing only
    logger.info(f"ACCEPT job {req.job_id} on gpu {req.gpu_id}: {req.image} {req.cmd}")
    return {"accepted": True, "node_id": NODE_ID, "gpu_id": req.gpu_id, "ts": time()}
