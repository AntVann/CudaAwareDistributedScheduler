from control_plane.core import persistence


class FakeCursor:
    def __init__(self, state):
        self.state = state
        self.fetchone_result = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params):
        normalized = " ".join(sql.split())
        if normalized == "SELECT active_policy FROM scheduler_settings WHERE singleton_key = %s":
            self.fetchone_result = self.state.get("active_policy")
            return
        if normalized.startswith("INSERT INTO scheduler_settings"):
            _singleton_key, active_policy, updated_by = params
            self.state["active_policy"] = (active_policy,)
            self.state["updated_by"] = updated_by
            return
        raise AssertionError(f"Unexpected SQL: {normalized}")

    def fetchone(self):
        return self.fetchone_result


class FakeConnection:
    def __init__(self, state):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return FakeCursor(self.state)


def test_get_active_policy_seeds_from_env_when_setting_absent(monkeypatch):
    state = {}
    monkeypatch.setattr(persistence, "pg_conn", lambda: FakeConnection(state))
    monkeypatch.setenv("SCHED_POLICY", "ROUND_ROBIN")

    active = persistence.get_active_policy()

    assert active.value == "ROUND_ROBIN"
    assert state["active_policy"] == ("ROUND_ROBIN",)
    assert state["updated_by"] == "startup"
