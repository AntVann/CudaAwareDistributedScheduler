import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from control_plane.core.models import JobSpec, JobStatus
from control_plane.core.persistence import enqueue_job, get_job_status

router = APIRouter(tags=["jobs"])
logger = logging.getLogger("control_plane.api.jobs")


class EnqueueResponse(BaseModel):
    job_id: str
    status: JobStatus


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
