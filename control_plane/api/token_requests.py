import logging
import os
import time
from collections import defaultdict
from typing import Dict, List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from control_plane.core.persistence import create_token_request

router = APIRouter(tags=["token-requests"])
logger = logging.getLogger("control_plane.api.token_requests")

_REQUEST_WINDOW_SECS = int(os.getenv("TOKEN_REQUEST_RATE_WINDOW_SECS", "600"))
_REQUEST_LIMIT = int(os.getenv("TOKEN_REQUEST_RATE_LIMIT", "20"))
_REQUEST_LOG: Dict[str, List[float]] = defaultdict(list)


class CreateTokenRequestBody(BaseModel):
    subject_name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=254)
    requested_projects: List[str] = Field(default_factory=list)
    purpose: str = Field(min_length=3, max_length=1000)


def _enforce_rate_limit(client_ip: str) -> None:
    now = time.time()
    entries = _REQUEST_LOG[client_ip]
    entries[:] = [ts for ts in entries if now - ts < _REQUEST_WINDOW_SECS]
    if len(entries) >= _REQUEST_LIMIT:
        raise HTTPException(status_code=429, detail="Too many token requests from this IP")
    entries.append(now)


@router.post("/token-requests", status_code=201)
def submit_token_request(body: CreateTokenRequestBody, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    _enforce_rate_limit(client_ip)

    projects = sorted({p.strip() for p in body.requested_projects if p.strip()})
    if not projects:
        raise HTTPException(status_code=400, detail="At least one project is required")
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="A valid email is required")

    try:
        return create_token_request(
            subject_name=body.subject_name.strip(),
            email=email,
            requested_projects=projects,
            purpose=body.purpose.strip(),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        logger.exception("Failed to submit token request for %s", body.email)
        raise HTTPException(status_code=500, detail="Failed to submit token request")
