import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from control_plane.core.models import JobSpec, JobState, JobStatus
from control_plane.core.persistence import (
    enqueue_job,
    get_job_spec,
    get_job_status,
    update_job_state,
)

router = APIRouter(tags=["jobs"])
logger = logging.getLogger("control_plane.api.jobs")


class EnqueueResponse(BaseModel):
    job_id: str
    status: JobStatus


class JobStartRequest(BaseModel):
    node_id: str
    gpu_ids: List[int] = Field(default_factory=list)


class JobFinishRequest(BaseModel):
    node_id: str
    success: bool
    exit_code: Optional[int] = None
    reason: Optional[str] = None


@router.post("/jobs", response_model=EnqueueResponse)
def create_job(spec: JobSpec):
    try:
        status = enqueue_job(spec)
        return EnqueueResponse(job_id=spec.job_id, status=status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to enqueue job %s", spec.job_id)
        raise HTTPException(status_code=500, detail="Failed to enqueue job") from exc


@router.get("/jobs/{job_id}", response_model=JobStatus)
def read_job(job_id: str):
    status = get_job_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return status


@router.get("/jobs/{job_id}/spec", response_model=JobSpec)
def read_job_spec(job_id: str):
    spec = get_job_spec(job_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Job spec not found")
    return spec


@router.post("/jobs/{job_id}/start", response_model=JobStatus)
def mark_job_running(job_id: str, req: JobStartRequest):
    try:
        return update_job_state(
            job_id,
            JobState.RUNNING,
            node_id=req.node_id,
            gpu_ids=req.gpu_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:
        logger.exception("Failed to mark job %s running", job_id)
        raise HTTPException(
            status_code=500, detail="Unable to update job status"
        ) from None


@router.post("/jobs/{job_id}/finish", response_model=JobStatus)
def finish_job(job_id: str, req: JobFinishRequest):
    new_state = JobState.DONE if req.success else JobState.FAILED
    try:
        return update_job_state(
            job_id,
            new_state,
            node_id=req.node_id,
            exit_code=req.exit_code,
            reason=req.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception:
        logger.exception("Failed to finish job %s", job_id)
        raise HTTPException(
            status_code=500, detail="Unable to update job status"
        ) from None
