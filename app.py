import os
from datetime import date, timedelta
from fastapi import FastAPI, HTTPException
from garminconnect import Garmin

app = FastAPI(title="Garmin Bridge", version="0.1.0")


def get_client() -> Garmin:
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if not email or not password:
        raise HTTPException(status_code=500, detail="GARMIN_EMAIL and GARMIN_PASSWORD are not configured")

    client = Garmin(email, password)
    client.login()
    return client


@app.get("/")
def root():
    return {"service": "garmin-bridge", "status": "ok"}


@app.get("/health/today")
def health_today():
    today = date.today().isoformat()
    try:
        client = get_client()
        data = client.get_user_summary(today)
        return {"date": today, "data": data}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Garmin request failed: {exc}") from exc


@app.get("/activities")
def activities(days: int = 7):
    if days < 1 or days > 30:
        raise HTTPException(status_code=400, detail="days must be between 1 and 30")

    end = date.today()
    start = end - timedelta(days=days - 1)
    try:
        client = get_client()
        data = client.get_activities_by_date(start.isoformat(), end.isoformat())
        return {"from": start.isoformat(), "to": end.isoformat(), "activities": data}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Garmin request failed: {exc}") from exc
