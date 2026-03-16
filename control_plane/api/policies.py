import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from control_plane.api.auth import require_admin, require_user_or_admin
from control_plane.core.models import SchedulerPolicy
from control_plane.core.persistence import get_active_policy, set_active_policy, supported_policies

router = APIRouter(tags=["policies"])
logger = logging.getLogger("control_plane.api.policies")


class PolicyUpdateRequest(BaseModel):
    policy: SchedulerPolicy


def _policy_response(active: SchedulerPolicy) -> dict[str, object]:
    return {"active": active.value, "supported": supported_policies()}


@router.get("/policies")
def list_policies(
    request: Request,
    _authorized=Depends(require_user_or_admin),
):
    """
    Expose available scheduling policies and the currently selected value.
    """
    scheduler = getattr(request.app.state, "scheduler", None)
    active = scheduler.active_policy if scheduler is not None else get_active_policy()
    return _policy_response(active)


@router.put("/policies/active")
def update_active_policy(
    body: PolicyUpdateRequest,
    request: Request,
    _authorized=Depends(require_admin),
):
    try:
        active = set_active_policy(body.policy.value, updated_by="operator")
        scheduler = getattr(request.app.state, "scheduler", None)
        if scheduler is not None:
            scheduler.set_active_policy(active)
        return _policy_response(active)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to update active policy")
        raise HTTPException(status_code=500, detail="Failed to update policy") from exc
