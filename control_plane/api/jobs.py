import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
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


@router.get("/jobs/{job_id}/logs")
def read_job_logs(
    job_id: str,
    request: Request,
    stream: str = Query("stderr", pattern="^(stdout|stderr)$"),
    tail: int = Query(200, ge=1, le=5000),
    principal: AuthPrincipal = Depends(require_user_or_admin),
) -> Dict[str, Any]:
    """
    Return the tail of the SLURM stdout/stderr file for a job.
    Requires a backend that exposes `read_logs(job_id, stream, tail)` (currently SLURM).
    Project-scoped: callers must own (or admin) the job's project.
    """
    if get_job_status(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")

    if not principal.is_admin:
        project = get_job_project(job_id)
        if project is None or project not in principal.projects:
            raise HTTPException(status_code=404, detail="Job not found")

    scheduler = getattr(request.app.state, "scheduler", None)
    backend = getattr(scheduler, "backend", None)
    if backend is None or not hasattr(backend, "read_logs"):
        raise HTTPException(
            status_code=501,
            detail="Log retrieval not supported by the current backend",
        )

    try:
        result = backend.read_logs(job_id, stream=stream, tail=tail)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to read logs for job %s", job_id)
        raise HTTPException(status_code=500, detail="Failed to read logs") from exc

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="No backend reference for this job (never submitted to SLURM)",
        )
    return result
