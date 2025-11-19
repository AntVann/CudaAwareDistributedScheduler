import os

from fastapi import APIRouter

router = APIRouter(tags=["policies"])

_SUPPORTED = ["FIFO", "ROUND_ROBIN", "BINPACK"]


@router.get("/policies")
def list_policies():
    """
    Expose available scheduling policies and the currently selected value.
    """
    active = os.getenv("SCHED_POLICY", "FIFO")
    if active not in _SUPPORTED:
        # Keep unknown choices visible for debugging but do not crash the API.
        computed_active = active
    else:
        computed_active = active
    return {"active": computed_active, "supported": _SUPPORTED}
