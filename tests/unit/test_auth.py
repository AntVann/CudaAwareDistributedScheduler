import pytest

from control_plane.api.auth import AuthError, authorize_bearer


@pytest.fixture(autouse=True)
def token_auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "token")
    monkeypatch.setenv("OPERATOR_API_TOKEN", "operator-secret")
    monkeypatch.setenv("AGENT_API_TOKEN", "agent-secret")


def test_authorize_bearer_returns_401_for_missing_token():
    with pytest.raises(AuthError) as exc_info:
        authorize_bearer("operator", None)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Missing bearer token"


def test_authorize_bearer_returns_401_for_invalid_token():
    with pytest.raises(AuthError) as exc_info:
        authorize_bearer("operator", "Bearer wrong-secret")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid bearer token"


def test_authorize_bearer_returns_403_for_wrong_scope_token():
    with pytest.raises(AuthError) as exc_info:
        authorize_bearer("operator", "Bearer agent-secret")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Bearer token lacks operator scope"


def test_authorize_bearer_accepts_valid_scoped_token():
    authorize_bearer("operator", "Bearer operator-secret")
