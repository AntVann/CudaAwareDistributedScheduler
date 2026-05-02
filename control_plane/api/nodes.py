import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request

from control_plane.api.auth import require_agent, require_user_or_admin
from control_plane.core.models import NodeInfo
from control_plane.core.persistence import list_nodes as persist_list_nodes
from control_plane.core.persistence import upsert_node as persist_upsert_node

router = APIRouter(tags=["nodes"])
logger = logging.getLogger("control_plane.api.nodes")


@router.get("/nodes", response_model=List[NodeInfo])
def list_nodes(request: Request, _authorized=Depends(require_user_or_admin)):
    """
    Return the current known nodes and their latest heartbeat payloads.

    If the configured execution backend exposes node discovery (e.g. SLURM
    via sinfo), use it as the source of truth. Otherwise read whatever was
    stored via agent heartbeats.
    """
    scheduler = getattr(request.app.state, "scheduler", None)
    backend = getattr(scheduler, "backend", None)
    if backend is not None and hasattr(backend, "list_nodes"):
        recent_secs = getattr(scheduler, "recent_secs", 30)
        try:
            return backend.list_nodes(recent_secs=recent_secs)
        except Exception:
            logger.exception("Backend list_nodes() failed; falling back to persisted heartbeats")
    return persist_list_nodes()


@router.post("/nodes", status_code=202)
def upsert_node(node: NodeInfo, _authorized: None = Depends(require_agent)):
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
