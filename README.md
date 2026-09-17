# Garmin Bridge

Cloud-hosted read-only bridge between Garmin Connect and the training analysis system.

## Endpoints

- `GET /` — health check
- `GET /health/today` — Garmin user summary for today
- `GET /activities?days=7` — activities from the last N days (1–30)

## Railway environment variables

Set these as Railway variables/secrets:

- `GARMIN_EMAIL`
- `GARMIN_PASSWORD`

Never commit Garmin credentials to GitHub.

## Local run

```bash
pip install -r requirements.txt
uvicorn app:app --reload
```

This project uses the community `garminconnect` Python library to access Garmin Connect. Garmin authentication/API behavior can change, so the bridge should be treated as an unofficial integration.
