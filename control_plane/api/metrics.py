import logging

from fastapi import APIRouter, HTTPException, Query, Request

from control_plane.core.persistence import read_metrics_summary

router = APIRouter(tags=["metrics"])
logger = logging.getLogger("control_plane.api.metrics")


@router.get("/metrics/summary")
def get_metrics_summary(
    request: Request,
    window_minutes: int = Query(default=60, ge=1),
):
    scheduler = getattr(request.app.state, "scheduler", None)
    fresh_node_seconds = scheduler.recent_secs if scheduler is not None else 30
    try:
        return read_metrics_summary(window_minutes=window_minutes, fresh_node_seconds=fresh_node_seconds)
    except Exception as exc:
        logger.exception("Failed to build metrics summary")
        raise HTTPException(status_code=500, detail="Failed to fetch metrics summary") from exc
