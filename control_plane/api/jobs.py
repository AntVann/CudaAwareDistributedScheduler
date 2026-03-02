import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from control_plane.api.auth import require_operator
from control_plane.core.models import JobSpec, JobStatus
from control_plane.core.persistence import enqueue_job, get_job_status, job_summary, list_jobs

router = APIRouter(tags=["jobs"])
logger = logging.getLogger("control_plane.api.jobs")


class EnqueueResponse(BaseModel):
    job_id: str
    created: bool
    status: JobStatus


@router.get("/jobs/summary")
def read_job_summary() -> Dict[str, int]:
    try:
        return job_summary()
    except Exception as exc:
        logger.exception("Failed to fetch job summary")
        raise HTTPException(status_code=500, detail="Failed to fetch job summary") from exc


@router.get("/jobs")
def read_jobs() -> List[Dict[str, Any]]:
    try:
        return list_jobs()
    except Exception as exc:
        logger.exception("Failed to list jobs")
        raise HTTPException(status_code=500, detail="Failed to list jobs") from exc


@router.post("/jobs", response_model=EnqueueResponse)
def create_job(
    spec: JobSpec,
    response: Response,
    _authorized: None = Depends(require_operator),
):
    try:
        status, created = enqueue_job(spec)
        response.status_code = 201 if created else 200
        return EnqueueResponse(job_id=spec.job_id, created=created, status=status)
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
