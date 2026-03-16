import pytest

from control_plane.api import auth
from control_plane.api.auth import AuthError, AuthPrincipal, authorize_bearer


@pytest.fixture(autouse=True)
def token_auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "token")
    monkeypatch.setenv("ADMIN_API_TOKEN", "admin-secret")
    monkeypatch.setenv("AGENT_API_TOKEN", "agent-secret")


def test_authorize_bearer_returns_401_for_missing_token():
    with pytest.raises(AuthError) as exc_info:
        authorize_bearer("user", None)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Missing bearer token"


def test_authorize_bearer_returns_401_for_invalid_token(monkeypatch):
    monkeypatch.setattr(auth, "resolve_human_token", lambda token: None)

    with pytest.raises(AuthError) as exc_info:
        authorize_bearer("user", "Bearer wrong-secret")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid or expired bearer token"


def test_authorize_bearer_returns_403_for_wrong_scope_token():
    with pytest.raises(AuthError) as exc_info:
        authorize_bearer("user", "Bearer agent-secret")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Bearer token lacks human scope"


def test_authorize_bearer_accepts_valid_user_token(monkeypatch):
    monkeypatch.setattr(
        auth,
        "resolve_human_token",
        lambda token: {
            "token_id": "tok-1",
            "subject": "alice",
            "role": "user",
            "projects": ["vision"],
            "expires_at": None,
        },
    )
    authorize_bearer("user", "Bearer user-secret")


def test_require_admin_rejects_non_admin():
    with pytest.raises(Exception):
        auth.require_admin(AuthPrincipal(token_id="1", subject="bob", role="user", projects=["x"]))
