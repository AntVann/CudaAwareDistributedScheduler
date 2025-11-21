import logging
from typing import List

from fastapi import APIRouter, HTTPException

from control_plane.core.models import NodeInfo
from control_plane.core.persistence import list_nodes as persist_list_nodes
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
