"""Garmin activities stored in Supabase (table public.activities).

Uses the same SUPABASE_URL / SUPABASE_SECRET_KEY as the token store. The full
Garmin JSON is kept so no data is lost; the view public.activity_overview
exposes the most useful fields as plain columns for analysis.
"""

import os
from datetime import datetime, timezone
from typing import Any

import requests

_TABLE = "activities"

# Fields in Garmin's activity list that only describe account permissions.
_DROPPED_SUMMARY_KEYS = {"userRoles"}


class ActivityStoreError(Exception):
    """Raised when activities cannot be read from or written to Supabase."""


def _start_time_gmt(summary: dict[str, Any]) -> str | None:
    value = summary.get("startTimeGMT")
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).isoformat()


class SupabaseActivityStore:
    def __init__(self, url: str, secret_key: str, timeout: float = 30) -> None:
        self._endpoint = f"{url.rstrip('/')}/rest/v1/{_TABLE}"
        self._headers = {"apikey": secret_key, "Content-Type": "application/json"}
        self._timeout = timeout

    @classmethod
    def from_env(cls) -> "SupabaseActivityStore | None":
        url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SECRET_KEY")
        if not url or not key:
            return None
        return cls(url, key)

    def _request(self, method: str, extra_headers: dict[str, str] | None = None, **kwargs: Any) -> requests.Response:
        try:
            resp = requests.request(
                method,
                self._endpoint,
                headers={**self._headers, **(extra_headers or {})},
                timeout=self._timeout,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise ActivityStoreError(f"Supabase request failed: {type(exc).__name__}") from None
        if not resp.ok:
            raise ActivityStoreError(f"Supabase {method} failed: HTTP {resp.status_code}")
        return resp

    def upsert_summaries(self, activities: list[dict[str, Any]]) -> int:
        """Insert or update activity summaries. Existing details/splits are kept."""
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            {
                "activity_id": activity["activityId"],
                "start_time_gmt": _start_time_gmt(activity),
                "summary": {k: v for k, v in activity.items() if k not in _DROPPED_SUMMARY_KEYS},
                "synced_at": now,
            }
            for activity in activities
            if activity.get("activityId")
        ]
        if rows:
            self._request(
                "POST",
                {"Prefer": "resolution=merge-duplicates,return=minimal"},
                params={"on_conflict": "activity_id"},
                json=rows,
            )
        return len(rows)

    def missing_details(self, limit: int) -> tuple[list[int], int]:
        """Newest activities without details, and how many are missing in total."""
        resp = self._request(
            "GET",
            {"Prefer": "count=exact"},
            params={
                "select": "activity_id",
                "details_synced_at": "is.null",
                "order": "start_time_gmt.desc.nullslast",
                "limit": str(limit),
            },
        )
        total = resp.headers.get("Content-Range", "*/0").rsplit("/", 1)[-1]
        return [row["activity_id"] for row in resp.json()], int(total) if total.isdigit() else 0

    def save_details(self, activity_id: int, details: Any, splits: Any) -> None:
        self._request(
            "PATCH",
            {"Prefer": "return=minimal"},
            params={"activity_id": f"eq.{activity_id}"},
            json={
                "details": details,
                "splits": splits,
                "details_synced_at": datetime.now(timezone.utc).isoformat(),
            },
        )
