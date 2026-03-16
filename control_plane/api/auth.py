import os
from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import Depends, Header, HTTPException

from control_plane.core.persistence import resolve_human_token


@dataclass(frozen=True)
class AuthSettings:
    mode: str
    agent_token: str
    admin_bootstrap_token: str


@dataclass(frozen=True)
class AuthPrincipal:
    token_id: str
    subject: str
    role: str
    projects: list[str]
    expires_at: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class AuthError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def load_auth_settings() -> AuthSettings:
    return AuthSettings(
        mode=os.getenv("AUTH_MODE", "none").strip().lower() or "none",
        agent_token=os.getenv("AGENT_API_TOKEN", "").strip(),
        admin_bootstrap_token=(
            os.getenv("ADMIN_API_TOKEN", "").strip() or os.getenv("OPERATOR_API_TOKEN", "").strip()
        ),
    )


def validate_auth_settings() -> None:
    settings = load_auth_settings()
    if settings.mode == "none":
        return
    if settings.mode != "token":
        raise RuntimeError(f"Unsupported AUTH_MODE: {settings.mode}")
    if not settings.agent_token:
        raise RuntimeError("AGENT_API_TOKEN is required when AUTH_MODE=token")


def _parse_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip() or None


def _raise_auth_error(exc: AuthError) -> None:
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    raise HTTPException(status_code=exc.status_code, detail=exc.detail, headers=headers) from exc


def _dev_admin_principal() -> AuthPrincipal:
    return AuthPrincipal(
        token_id="dev-mode",
        subject="dev-mode",
        role="admin",
        projects=["*"],
        expires_at=None,
    )


def _authenticate_human_bearer(authorization: str | None) -> AuthPrincipal:
    settings = load_auth_settings()
    if settings.mode == "none":
        return _dev_admin_principal()
    if settings.mode != "token":
        raise AuthError(500, "Invalid auth configuration")

    token = _parse_bearer_token(authorization)
    if token is None:
        raise AuthError(401, "Missing bearer token")
    if token == settings.agent_token:
        raise AuthError(403, "Bearer token lacks human scope")

    resolved = resolve_human_token(token)
    if resolved is None:
        raise AuthError(401, "Invalid or expired bearer token")

    role = str(resolved["role"]).lower()
    if role not in {"admin", "user"}:
        raise AuthError(403, "Bearer token lacks human scope")

    projects = resolved.get("projects") or []
    if isinstance(projects, str):
        projects = [projects]

    expires_at = resolved.get("expires_at")
    expires_iso = expires_at.isoformat() if expires_at else None
    return AuthPrincipal(
        token_id=str(resolved["token_id"]),
        subject=str(resolved["subject"]),
        role=role,
        projects=list(projects),
        expires_at=expires_iso,
    )


def _authenticate_agent_bearer(authorization: str | None) -> None:
    settings = load_auth_settings()
    if settings.mode == "none":
        return
    if settings.mode != "token":
        raise AuthError(500, "Invalid auth configuration")

    token = _parse_bearer_token(authorization)
    if token is None:
        raise AuthError(401, "Missing bearer token")
    if token != settings.agent_token:
        raise AuthError(403, "Bearer token lacks agent scope")


def require_human_principal(authorization: str | None = Header(default=None)) -> AuthPrincipal:
    try:
        return _authenticate_human_bearer(authorization)
    except AuthError as exc:
        _raise_auth_error(exc)


def require_user_or_admin(principal: AuthPrincipal = Depends(require_human_principal)) -> AuthPrincipal:
    if principal.role not in {"admin", "user"}:
        raise HTTPException(status_code=403, detail="Bearer token lacks user scope")
    return principal


def require_admin(principal: AuthPrincipal = Depends(require_human_principal)) -> AuthPrincipal:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="Bearer token lacks admin scope")
    return principal


def require_agent(authorization: str | None = Header(default=None)) -> None:
    try:
        _authenticate_agent_bearer(authorization)
    except AuthError as exc:
        _raise_auth_error(exc)


def authorize_bearer(required_scope: str, authorization: str | None) -> None:
    """
    Backward-compatible helper used by existing unit tests.
    """
    if required_scope == "agent":
        _authenticate_agent_bearer(authorization)
        return

    principal = _authenticate_human_bearer(authorization)
    if required_scope == "admin" and principal.role != "admin":
        raise AuthError(403, "Bearer token lacks admin scope")
    if required_scope in {"user", "operator"} and principal.role not in {"admin", "user"}:
        raise AuthError(403, f"Bearer token lacks {required_scope} scope")


def require_scope(required_scope: str) -> Callable[[Optional[str]], None]:
    def dependency(authorization: str | None = Header(default=None)) -> None:
        try:
            authorize_bearer(required_scope, authorization)
        except AuthError as exc:
            _raise_auth_error(exc)

    return dependency


# Backward-compatible alias for existing code/tests.
require_operator = require_user_or_admin
