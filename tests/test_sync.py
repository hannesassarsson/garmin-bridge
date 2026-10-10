"""Offline tests for activity sync: fake Garmin and fake Supabase REST API."""

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import activity_store  # noqa: E402
import app as bridge  # noqa: E402

AUTH = {"Authorization": "Bearer test-key"}
TOKENS = json.dumps({"di_token": "a", "di_refresh_token": "r", "di_client_id": "c"})


def activity(activity_id: int, day: int) -> dict:
    return {
        "activityId": activity_id,
        "startTimeGMT": f"2026-10-{day:02d} 08:00:00",
        "activityName": f"Pass {activity_id}",
        "userRoles": ["SCOPE_X"],
    }


HISTORY = [activity(i, 1 + i % 28) for i in range(120, 0, -1)]  # newest first


class FakeActivitiesApi:
    """In-memory stand-in for PostgREST on public.activities."""

    def __init__(self) -> None:
        self.rows: dict[int, dict] = {}
        self.fail = False

    def request(self, method, url, headers, timeout, params=None, json=None):
        assert headers["apikey"] == "sb_secret_test"

        class Resp:
            def __init__(self, status=200, body=None, extra=None):
                self.status_code = status
                self.ok = 200 <= status < 300
                self._body = body
                self.headers = extra or {}

            def json(self):
                return self._body

        if self.fail:
            return Resp(500)
        if method == "POST":
            for row in json:
                self.rows.setdefault(row["activity_id"], {}).update(row)
            return Resp(201)
        if method == "GET":
            missing = sorted(
                (r for r in self.rows.values() if not r.get("details_synced_at")),
                key=lambda r: r["start_time_gmt"],
                reverse=True,
            )
            page = [{"activity_id": r["activity_id"]} for r in missing[: int(params["limit"])]]
            return Resp(200, page, {"Content-Range": f"0-{len(page)}/{len(missing)}"})
        if method == "PATCH":
            self.rows[int(params["activity_id"].removeprefix("eq."))].update(json)
            return Resp(204)
        raise AssertionError(method)


class FakeInner:
    def dumps(self):
        return TOKENS


class FakeGarmin:
    calls: list = []

    def __init__(self, email, password):
        self.client = FakeInner()

    def login(self, tokenstore=None):
        pass

    def get_activities_by_date(self, start, end):
        FakeGarmin.calls.append("by_date")
        return HISTORY[:2]

    def get_activities(self, start, limit):
        FakeGarmin.calls.append("page")
        return HISTORY[start : start + limit]

    def get_activity(self, activity_id):
        FakeGarmin.calls.append("details")
        return {"summaryDTO": {"trainingStressScore": 50}}

    def get_activity_splits(self, activity_id):
        FakeGarmin.calls.append("splits")
        return {"lapDTOs": [{"distance": 1000}]}


@pytest.fixture
def api(monkeypatch, tmp_path):
    fake = FakeActivitiesApi()
    monkeypatch.setattr(activity_store.requests, "request", fake.request)
    monkeypatch.setattr(bridge.SupabaseTokenStore, "from_env", classmethod(lambda cls: None))
    monkeypatch.setattr(bridge, "Garmin", FakeGarmin)
    monkeypatch.setattr(bridge, "_client", None)
    monkeypatch.setattr(bridge, "_login_blocked_until", 0.0)
    monkeypatch.setattr(bridge, "SYNC_REQUEST_DELAY_SECONDS", 0)
    monkeypatch.setenv("GARMIN_TOKENSTORE", str(tmp_path))
    FakeGarmin.calls = []
    for key, value in {
        "BRIDGE_API_KEY": "test-key",
        "GARMIN_EMAIL": "user@example.com",
        "GARMIN_PASSWORD": "pw",
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SECRET_KEY": "sb_secret_test",
    }.items():
        monkeypatch.setenv(key, value)
    return fake


def test_sync_requires_api_key(api):
    assert TestClient(bridge.app).post("/sync").status_code == 401


def test_daily_sync_saves_activities_and_details(api):
    resp = TestClient(bridge.app).post("/sync?days=3&max_details=10", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json() == {"activities_saved": 2, "details_synced": 2, "details_remaining": 0}
    row = api.rows[120]
    assert "userRoles" not in row["summary"]
    assert row["start_time_gmt"].endswith("+00:00")
    assert row["splits"] == {"lapDTOs": [{"distance": 1000}]}
    assert row["details_synced_at"]


def test_backfill_pages_through_history(api):
    client = TestClient(bridge.app)
    first = client.post("/sync/backfill?start=0&limit=50", headers=AUTH).json()
    assert first == {"activities_saved": 50, "next_start": 50, "done": False}
    client.post("/sync/backfill?start=50&limit=50", headers=AUTH)
    last = client.post("/sync/backfill?start=100&limit=50", headers=AUTH).json()
    assert last == {"activities_saved": 20, "next_start": 120, "done": True}
    assert len(api.rows) == 120
    assert "details" not in FakeGarmin.calls  # backfill pages only fetch summaries


def test_details_are_filled_gradually(api):
    client = TestClient(bridge.app)
    client.post("/sync/backfill?start=0&limit=100", headers=AUTH)
    result = client.post("/sync/details?max_details=15", headers=AUTH).json()
    assert result == {"details_synced": 15, "details_remaining": 85}


def test_resync_keeps_existing_details(api):
    client = TestClient(bridge.app)
    client.post("/sync?days=3", headers=AUTH)
    client.post("/sync?days=3", headers=AUTH)
    assert api.rows[120]["details"] == {"summaryDTO": {"trainingStressScore": 50}}
    assert FakeGarmin.calls.count("details") == 2  # not fetched again


def test_storage_failure_returns_503(api):
    api.fail = True
    assert TestClient(bridge.app).post("/sync", headers=AUTH).status_code == 503


def test_sync_needs_supabase(api, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL")
    assert TestClient(bridge.app).post("/sync", headers=AUTH).status_code == 503
