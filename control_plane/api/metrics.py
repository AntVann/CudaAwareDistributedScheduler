import os
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from control_plane.api.auth import AuthPrincipal, require_user_or_admin
from control_plane.core.persistence import read_metrics_summary

router = APIRouter(tags=["metrics"])
logger = logging.getLogger("control_plane.api.metrics")


@router.get("/metrics/summary")
def get_metrics_summary(
    request: Request,
    window_minutes: int = Query(default=60, ge=1),
    principal: AuthPrincipal = Depends(require_user_or_admin),
):
    scheduler = getattr(request.app.state, "scheduler", None)
    fresh_node_seconds = scheduler.recent_secs if scheduler is not None else 30
    try:
        summary = read_metrics_summary(
            window_minutes=window_minutes,
            fresh_node_seconds=fresh_node_seconds,
            is_admin=principal.is_admin,
            projects=principal.projects,
        )
        backend = getattr(scheduler, "backend", None)
        if os.getenv("BACKEND", "redis-agent").strip().lower() == "slurm" and backend is not None:
            nodes = backend.list_nodes(recent_secs=fresh_node_seconds)
            fresh_cutoff = time.time() - fresh_node_seconds
            fresh = sum(1 for node in nodes if float(node.last_seen or 0.0) >= fresh_cutoff)
            summary["nodes"] = {
                "total": len(nodes),
                "fresh": fresh,
                "stale": len(nodes) - fresh,
            }
        return summary
    except Exception as exc:
        logger.exception("Failed to build metrics summary")
        raise HTTPException(status_code=500, detail="Failed to fetch metrics summary") from exc
