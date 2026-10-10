"""Offline tests: no Garmin or Supabase traffic. Run with `pytest`."""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as bridge  # noqa: E402
import token_store  # noqa: E402

TOKENS = json.dumps({"di_token": "access-1", "di_refresh_token": "refresh-1", "di_client_id": "client"})
AUTH = {"Authorization": "Bearer test-key"}


class FakeSupabase:
    """Stands in for requests.get/post against the Supabase REST API."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.fail = False
        self.writes = 0

    def _resp(self, status: int, body=None):
        class Resp:
            status_code = status
            ok = 200 <= status < 300

            def json(self):
                return body

        return Resp()

    def get(self, url, headers, params, timeout):
        assert headers["apikey"] == "sb_secret_test"
        if self.fail:
            return self._resp(500)
        row = self.rows.get(params["id"].removeprefix("eq."))
        return self._resp(200, [{"token_ciphertext": row["token_ciphertext"]}] if row else [])

    def post(self, url, headers, params, json, timeout):
        assert "merge-duplicates" in headers["Prefer"]
        if self.fail:
            return self._resp(500)
        self.writes += 1
        self.rows[json["id"]] = json
        return self._resp(201)


class FakeInnerClient:
    def __init__(self) -> None:
        self.tokens = TOKENS

    def dumps(self) -> str:
        return self.tokens


class FakeGarmin:
    instances: list["FakeGarmin"] = []
    fail_login = False

    def __init__(self, email, password) -> None:
        self.client = FakeInnerClient()
        self.login_args: list = []
        FakeGarmin.instances.append(self)

    def login(self, tokenstore=None):
        self.login_args.append(tokenstore)
        if FakeGarmin.fail_login:
            raise RuntimeError("boom")

    def get_activities_by_date(self, start, end):
        return [{"activityName": "Zwift"}]


@pytest.fixture
def env(monkeypatch):
    fake = FakeSupabase()
    monkeypatch.setattr(token_store.requests, "get", fake.get)
    monkeypatch.setattr(token_store.requests, "post", fake.post)
    monkeypatch.setattr(bridge, "Garmin", FakeGarmin)
    monkeypatch.setattr(bridge, "_client", None)
    monkeypatch.setattr(bridge, "_last_saved_tokens", None)
    monkeypatch.setattr(bridge, "_login_blocked_until", 0.0)
    FakeGarmin.instances = []
    FakeGarmin.fail_login = False
    for key, value in {
        "BRIDGE_API_KEY": "test-key",
        "GARMIN_EMAIL": "user@example.com",
        "GARMIN_PASSWORD": "pw",
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_test",
        "TOKEN_ENCRYPTION_KEY": "a-long-random-test-key",
    }.items():
        monkeypatch.setenv(key, value)
    return fake


def make_store(key="a-long-random-test-key"):
    return token_store.SupabaseTokenStore("https://example.supabase.co", "sb_secret_test", key)


def test_roundtrip_is_encrypted(env):
    make_store().save(TOKENS)
    stored = env.rows["default"]["token_ciphertext"]
    assert stored.startswith("v1:")
    assert "refresh-1" not in stored
    assert make_store().load() == TOKENS


def test_wrong_encryption_key_is_rejected(env):
    make_store().save(TOKENS)
    with pytest.raises(token_store.TokenStoreError):
        make_store("another-key").load()


def test_partial_config_is_an_error(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    with pytest.raises(token_store.TokenStoreError):
        token_store.SupabaseTokenStore.from_env()


def test_first_login_saves_tokens(env):
    resp = TestClient(bridge.app).get("/activities?days=1", headers=AUTH)
    assert resp.status_code == 200
    assert FakeGarmin.instances[0].login_args == [None]  # credential login, nothing stored yet
    assert make_store().load() == TOKENS


def test_restart_reuses_stored_tokens(env):
    make_store().save(TOKENS)
    writes_before = env.writes
    resp = TestClient(bridge.app).get("/activities?days=1", headers=AUTH)
    assert resp.status_code == 200
    assert FakeGarmin.instances[0].login_args == [TOKENS]  # inline tokens, no password login
    assert env.writes == writes_before  # unchanged tokens are not rewritten


def test_refreshed_tokens_are_saved(env):
    make_store().save(TOKENS)
    client = TestClient(bridge.app)
    client.get("/activities?days=1", headers=AUTH)
    refreshed = json.dumps({"di_token": "access-2", "di_refresh_token": "refresh-2", "di_client_id": "client"})
    FakeGarmin.instances[0].client.tokens = refreshed
    client.get("/activities?days=1", headers=AUTH)
    assert make_store().load() == refreshed


def test_supabase_outage_does_not_trigger_garmin_login(env):
    env.fail = True
    resp = TestClient(bridge.app).get("/activities?days=1", headers=AUTH)
    assert resp.status_code == 503
    assert FakeGarmin.instances[0].login_args == []


def test_failed_login_pauses_further_attempts(env):
    FakeGarmin.fail_login = True
    client = TestClient(bridge.app)
    assert client.get("/activities?days=1", headers=AUTH).status_code == 502
    assert client.get("/activities?days=1", headers=AUTH).status_code == 503
    assert len(FakeGarmin.instances) == 1  # second request never reached Garmin


def test_auth_status_does_not_contact_garmin(env):
    client = TestClient(bridge.app)
    assert client.get("/auth/status").status_code == 401
    assert client.get("/auth/status", headers=AUTH).json()["stored_tokens"] == "missing"
    make_store().save(TOKENS)
    body = client.get("/auth/status", headers=AUTH).json()
    assert body == {
        "garmin_session_active": False,
        "login_paused_seconds": 0,
        "token_storage": "supabase",
        "stored_tokens": "present",
    }
    assert FakeGarmin.instances == []
