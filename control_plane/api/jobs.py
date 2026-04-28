import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from control_plane.api.auth import AuthPrincipal, require_user_or_admin
from control_plane.core.models import JobSpec, JobStatus
from control_plane.core.persistence import enqueue_job, get_job_project, get_job_status, job_summary, list_jobs

router = APIRouter(tags=["jobs"])
logger = logging.getLogger("control_plane.api.jobs")


class EnqueueResponse(BaseModel):
    job_id: str
    created: bool
    status: JobStatus


@router.get("/jobs/summary")
def read_job_summary(principal: AuthPrincipal = Depends(require_user_or_admin)) -> Dict[str, int]:
    try:
        return job_summary(is_admin=principal.is_admin, projects=principal.projects)
    except Exception as exc:
        logger.exception("Failed to fetch job summary")
        raise HTTPException(status_code=500, detail="Failed to fetch job summary") from exc


@router.get("/jobs")
def read_jobs(principal: AuthPrincipal = Depends(require_user_or_admin)) -> List[Dict[str, Any]]:
    try:
        return list_jobs(is_admin=principal.is_admin, projects=principal.projects)
    except Exception as exc:
        logger.exception("Failed to list jobs")
        raise HTTPException(status_code=500, detail="Failed to list jobs") from exc


@router.post("/jobs", response_model=EnqueueResponse)
def create_job(
    spec: JobSpec,
    response: Response,
    principal: AuthPrincipal = Depends(require_user_or_admin),
):
    if not spec.project.strip():
        raise HTTPException(status_code=400, detail="project is required")
    if not principal.is_admin and spec.project not in principal.projects:
        raise HTTPException(status_code=403, detail="Bearer token cannot submit to this project")
    try:
        status, created = enqueue_job(spec, submitted_by=principal.subject)
        response.status_code = 201 if created else 200
        return EnqueueResponse(job_id=spec.job_id, created=created, status=status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to enqueue job %s", spec.job_id)
        raise HTTPException(status_code=500, detail="Failed to enqueue job") from exc


@router.get("/jobs/{job_id}", response_model=JobStatus)
def read_job(
    job_id: str,
    principal: AuthPrincipal = Depends(require_user_or_admin),
):
    if not principal.is_admin:
        project = get_job_project(job_id)
        if project is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if project not in principal.projects:
            raise HTTPException(status_code=404, detail="Job not found")
    status = get_job_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return status
