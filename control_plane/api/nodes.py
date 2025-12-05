import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Response

from control_plane.core.models import JobAssignment, NodeInfo
from control_plane.core.persistence import get_job_spec
from control_plane.core.persistence import list_nodes as persist_list_nodes
from control_plane.core.persistence import (
    pop_assignment_for_node as persist_pop_assignment,
)
from control_plane.core.persistence import upsert_node as persist_upsert_node

router = APIRouter(tags=["nodes"])
logger = logging.getLogger("control_plane.api.nodes")


@router.get("/nodes", response_model=List[NodeInfo])
def list_nodes():
    """
    Return the current known nodes and their latest heartbeat payloads.
    """
    return persist_list_nodes()


@router.post("/nodes", status_code=202)
def upsert_node(node: NodeInfo):
    """
    Accept heartbeat payloads from agents. Upserts rows and refreshes last_seen.
    """
    try:
        persist_upsert_node(node)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to upsert node %s", node.node_id)
        raise HTTPException(status_code=500, detail="Failed to save node heartbeat") from exc
    return {"ok": True}


@router.post(
    "/nodes/{node_id}/assignments/next",
    response_model=Optional[JobAssignment],
    status_code=200,
)
def next_assignment(node_id: str, response: Response):
    """
    Fetch the next queued job for a node. Returns 204 when no work is available.
    """
    job_id = persist_pop_assignment(node_id)
    if not job_id:
        response.status_code = 204
        return None
    spec = get_job_spec(job_id)
    if spec is None:
        logger.warning("Assignment popped for %s but spec missing (job=%s)", node_id, job_id)
        response.status_code = 404
        return None
    return JobAssignment(job_id=job_id, spec=spec)
