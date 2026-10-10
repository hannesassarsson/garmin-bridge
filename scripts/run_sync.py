"""Call the bridge's sync endpoints. Used by .github/workflows/garmin-sync.yml.

Usage: python scripts/run_sync.py daily|backfill|health
Needs BRIDGE_URL and BRIDGE_API_KEY. Prints counts only, never activity data.
A failed sync request is never retried, so a Garmin problem cannot turn into
repeated login attempts.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BRIDGE_URL", "https://garmin-bridge-og07.onrender.com").rstrip("/")
API_KEY = os.environ.get("BRIDGE_API_KEY", "")
PAUSE_SECONDS = 5
MAX_DETAIL_ROUNDS = 200
HEALTH_CHUNK_DAYS = 14
MAX_HEALTH_DAYS = 730


def call(path: str) -> dict:
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        method="POST",
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        sys.exit(f"{path.split('?')[0]} failed with HTTP {exc.code}: {detail}")


def daily() -> None:
    print("Daily sync:", call("/sync?days=3&max_details=10"))


def backfill() -> None:
    start = 0
    while True:
        result = call(f"/sync/backfill?start={start}&limit=50")
        print("History page:", result)
        if result["done"]:
            break
        start = result["next_start"]
        time.sleep(PAUSE_SECONDS)

    for _ in range(MAX_DETAIL_ROUNDS):
        result = call("/sync/details?max_details=15")
        print("Details:", result)
        if result["details_remaining"] <= 0:
            return
        time.sleep(PAUSE_SECONDS)
    print("Stopped after the maximum number of rounds; run the backfill again to continue.")


def health() -> None:
    """Store daily health data going back in time until Garmin has no more data."""
    for offset in range(0, MAX_HEALTH_DAYS, HEALTH_CHUNK_DAYS):
        result = call(f"/sync/health?offset={offset}&days={HEALTH_CHUNK_DAYS}")
        print(f"Health, {offset}-{offset + HEALTH_CHUNK_DAYS - 1} days back:", result)
        if offset and result["health_days_with_data"] == 0:
            return
        time.sleep(PAUSE_SECONDS)


if __name__ == "__main__":
    if not API_KEY:
        sys.exit("BRIDGE_API_KEY is missing. Add it under Settings -> Secrets and variables -> Actions.")
    mode = sys.argv[1] if len(sys.argv) > 1 else "daily"
    {"daily": daily, "backfill": backfill, "health": health}[mode]()
