from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from enum import Enum

class JobState(str, Enum):
    QUEUED = "QUEUED"
    PLACED = "PLACED"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class JobSpec(BaseModel):
    job_id: str
    image: str
    cmd: List[str]
    gpus: int = 1
    cpu: Optional[int] = None
    mem_gb: Optional[float] = None
    priority: int = 0
    env: Dict[str, str] = {}
    metadata: Dict[str, Any] = {}

class JobStatus(BaseModel):
    job_id: str
    state: JobState
    node_id: Optional[str] = None
    gpu_ids: List[int] = []
    timestamps: Dict[str, Optional[float]] = {}
    exit_code: Optional[int] = None
    reason: Optional[str] = None

class GpuInfo(BaseModel):
    index: int
    name: str
    mem_total_mb: int
    utilization: float = 0.0
    mem_used_mb: int = 0
    temperature: Optional[int] = None

class NodeInfo(BaseModel):
    node_id: str
    gpus: List[GpuInfo] = []
    labels: Dict[str, str] = {}
    agent_health: Dict[str, float] = {}
