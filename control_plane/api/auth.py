import os
from dataclasses import dataclass
from typing import Callable

from fastapi import Header, HTTPException


@dataclass(frozen=True)
class AuthSettings:
    mode: str
    operator_token: str
    agent_token: str


class AuthError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def load_auth_settings() -> AuthSettings:
    return AuthSettings(
        mode=os.getenv("AUTH_MODE", "none").strip().lower() or "none",
        operator_token=os.getenv("OPERATOR_API_TOKEN", "").strip(),
        agent_token=os.getenv("AGENT_API_TOKEN", "").strip(),
    )


def validate_auth_settings() -> None:
    settings = load_auth_settings()
    if settings.mode == "none":
        return
    if settings.mode != "token":
        raise RuntimeError(f"Unsupported AUTH_MODE: {settings.mode}")
    if not settings.operator_token:
        raise RuntimeError("OPERATOR_API_TOKEN is required when AUTH_MODE=token")
    if not settings.agent_token:
        raise RuntimeError("AGENT_API_TOKEN is required when AUTH_MODE=token")
    if settings.operator_token == settings.agent_token:
        raise RuntimeError("OPERATOR_API_TOKEN and AGENT_API_TOKEN must differ")


def authorize_bearer(required_scope: str, authorization: str | None) -> None:
    settings = load_auth_settings()
    if settings.mode == "none":
        return
    if settings.mode != "token":
        raise AuthError(500, "Invalid auth configuration")

    token = _parse_bearer_token(authorization)
    if token is None:
        raise AuthError(401, "Missing bearer token")

    actual_scope = _scope_for_token(token, settings)
    if actual_scope is None:
        raise AuthError(401, "Invalid bearer token")
    if actual_scope != required_scope:
        raise AuthError(403, f"Bearer token lacks {required_scope} scope")


def require_scope(required_scope: str) -> Callable[[str | None], None]:
    def dependency(authorization: str | None = Header(default=None)) -> None:
        try:
            authorize_bearer(required_scope, authorization)
        except AuthError as exc:
            headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
                headers=headers,
            ) from exc

    return dependency


require_operator = require_scope("operator")
require_agent = require_scope("agent")


def _parse_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip() or None


def _scope_for_token(token: str, settings: AuthSettings) -> str | None:
    if token == settings.operator_token:
        return "operator"
    if token == settings.agent_token:
        return "agent"
    return None
