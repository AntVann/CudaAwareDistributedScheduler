import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from control_plane.core.persistence import set_job_state

router = APIRouter(tags=["admin"])
logger = logging.getLogger("control_plane.api.admin")


class StateReq(BaseModel):
    state: str
    exit_code: int | None = None


@router.post("/admin/jobs/{job_id}/state")
def set_state(job_id: str, body: StateReq):
    try:
        set_job_state(job_id, body.state, body.exit_code)
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        logger.exception("Failed to set state for job %s", job_id)
        raise HTTPException(status_code=500, detail="Failed to update job state")
