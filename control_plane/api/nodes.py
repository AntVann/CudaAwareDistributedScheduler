import logging

from fastapi import APIRouter, HTTPException

router = APIRouter(tags=["nodes"])
logger = logging.getLogger("control_plane.api.nodes")


@router.get("/nodes")
def list_nodes():
    """
    Placeholder list endpoint until Milestone 3 lands.
    """
    return []


@router.post("/nodes")
def upsert_node():
    """
    Placeholder upsert endpoint for heartbeat payloads (Milestone 3).
    """
    logger.warning("Node upsert called before implementation")
    raise HTTPException(
        status_code=501,
        detail="Node upsert/list arrives in Milestone 3; not implemented yet.",
    )
