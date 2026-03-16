import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from control_plane.api.auth import AuthPrincipal, require_admin, require_agent
from control_plane.core.mail import send_token_email
from control_plane.core.persistence import (
    approve_token_request,
    list_api_tokens,
    list_token_requests,
    reject_token_request,
    revoke_api_token,
    set_job_state,
)

router = APIRouter(tags=["admin"])
logger = logging.getLogger("control_plane.api.admin")


class StateReq(BaseModel):
    state: str
    exit_code: int | None = None
    reason: str | None = None


class TokenRequestReviewBody(BaseModel):
    review_notes: Optional[str] = None


class RevokeTokenBody(BaseModel):
    reason: Optional[str] = None


@router.post("/admin/jobs/{job_id}/state")
def set_state(
    job_id: str,
    body: StateReq,
    _authorized: None = Depends(require_agent),
):
    try:
        set_job_state(job_id, body.state, body.exit_code, body.reason)
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except Exception:
        logger.exception("Failed to set state for job %s", job_id)
        raise HTTPException(status_code=500, detail="Failed to update job state")


@router.get("/admin/token-requests")
def get_token_requests(
    status: Optional[str] = Query(default=None),
    _principal: AuthPrincipal = Depends(require_admin),
):
    normalized = status.upper() if status else None
    if normalized and normalized not in {"PENDING", "APPROVED", "REJECTED"}:
        raise HTTPException(status_code=400, detail="Unsupported status filter")

    try:
        return list_token_requests(status=normalized)
    except Exception:
        logger.exception("Failed to list token requests")
        raise HTTPException(status_code=500, detail="Failed to list token requests")


@router.post("/admin/token-requests/{request_id}/approve")
def approve_request(
    request_id: str,
    body: TokenRequestReviewBody,
    principal: AuthPrincipal = Depends(require_admin),
):
    try:
        return approve_token_request(
            request_id=request_id,
            reviewed_by=principal.subject,
            review_notes=body.review_notes,
            role="user",
            ttl_days=90,
            deliver=send_token_email,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception:
        logger.exception("Failed to approve token request %s", request_id)
        raise HTTPException(status_code=500, detail="Failed to approve token request")


@router.post("/admin/token-requests/{request_id}/reject")
def reject_request(
    request_id: str,
    body: TokenRequestReviewBody,
    principal: AuthPrincipal = Depends(require_admin),
):
    try:
        return reject_token_request(
            request_id=request_id,
            reviewed_by=principal.subject,
            review_notes=body.review_notes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except Exception:
        logger.exception("Failed to reject token request %s", request_id)
        raise HTTPException(status_code=500, detail="Failed to reject token request")


@router.get("/admin/tokens")
def get_tokens(_principal: AuthPrincipal = Depends(require_admin)):
    try:
        return list_api_tokens()
    except Exception:
        logger.exception("Failed to list API tokens")
        raise HTTPException(status_code=500, detail="Failed to list API tokens")


@router.post("/admin/tokens/{token_id}/revoke")
def revoke_token(
    token_id: str,
    body: RevokeTokenBody,
    principal: AuthPrincipal = Depends(require_admin),
):
    _ = body.reason
    try:
        return revoke_api_token(token_id, revoked_by=principal.subject)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except Exception:
        logger.exception("Failed to revoke token %s", token_id)
        raise HTTPException(status_code=500, detail="Failed to revoke token")
