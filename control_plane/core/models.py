from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

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
    env: Dict[str, str] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class JobStatus(BaseModel):
    state: JobState
    node_id: Optional[str] = None
    gpu_ids: List[int] = Field(default_factory=list)
    timestamps: Dict[str, Optional[float]] = Field(default_factory=dict)
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
    gpus: List[GpuInfo] = Field(default_factory=list)
    labels: Dict[str, str] = Field(default_factory=dict)
    agent_health: Dict[str, float] = Field(default_factory=dict)
    last_seen: Optional[float] = None


class JobAssignment(BaseModel):
    job_id: str
    spec: JobSpec
