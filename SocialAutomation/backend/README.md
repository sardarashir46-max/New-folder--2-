# Backend

Django REST API for SocialAutomation.

## Initial setup

```bash
python -m venv venv
# Windows CMD: venv\\Scripts\\activate
pip install -r requirements.txt
```

If `backend/.env` does not already exist, copy `.env.example` to `.env`. Preserve any existing `.env` and add or update only the required keys. Set a random `DJANGO_SECRET_KEY` and `YOUTUBE_API_KEY` from Google Cloud Console. Enable **YouTube Data API v3** and restrict the API key to that API. Never commit `.env`.

Then run:

```bash
python manage.py migrate
python manage.py runserver
```

The authentication API is under `/api/auth/`. Unsafe session-authenticated requests require CSRF.

## Selected YouTube channel discovery

- Add a channel: `POST /api/channels/` with `{"channel_url":"https://www.youtube.com/@handle"}` or a channel ID.
- List/remove channels: `GET/DELETE /api/channels/` and `/api/channels/{id}/`.
- Refresh metadata and discover latest uploads: `POST /api/channels/{id}/refresh/`.
- List discovered video metadata: `GET /api/channels/{id}/videos/`.
- Mark rights after confirming permission: `PATCH /api/channels/{id}/videos/` with `video_id` and `rights_status` (`owned`, `authorized`, or `rejected`).
- Toggle monitoring preference: `POST /api/channels/{id}/monitoring/` with `{"enabled":true}`.
- View owner-only monitoring totals and recent discoveries: `GET /api/channels/monitoring-summary/`.

This milestone discovers public metadata only. It does not download video files or grant permission to reuse them. A rights status is the user's declaration, not independently verified proof or a licence. Only content the user owns or is explicitly authorized to redistribute can enter a future publishing workflow.

## Automatic YouTube monitoring

Monitoring uses Celery workers, Celery Beat, and Redis. The default `.env.example` settings poll enabled channels every 900 seconds. Set `YOUTUBE_MONITORING_ENABLED=False` to stop new scheduled checks, or change `YOUTUBE_MONITOR_INTERVAL_SECONDS` (minimum 60 seconds). `REDIS_URL` selects the broker. Temporary network/server/rate-limit/quota errors are retried with exponential backoff; permanent API configuration errors are logged without repeated retries. Disabled channels are excluded from dispatch and checked again by the worker before saving results. The application stores metadata only and never downloads video files.

Install/start Redis first. With Docker, a local Redis can be started in its own terminal using `docker run --rm --name socialautomation-redis -p 6379:6379 redis:7-alpine`. Configure the existing `backend/.env` with `REDIS_URL`, `YOUTUBE_MONITORING_ENABLED`, and `YOUTUBE_MONITOR_INTERVAL_SECONDS`, preserving existing values, then run these processes in separate terminals from `backend/`:

```bash
py manage.py runserver
py -m celery -A config worker --pool=solo --loglevel=INFO
py -m celery -A config beat --loglevel=INFO
```

Run only one Celery Beat scheduler for this project so a periodic check is not dispatched twice. The `solo` pool is suitable for local Windows development; use a supported worker pool on Linux deployments. Redis must be available to the Beat scheduler and workers. Channel creation/manual refresh still use the YouTube API synchronously; scheduled monitoring never runs inside a Django API request. The dashboard refreshes channel status and recent discoveries once per minute while open.

Login attempts are limited to 5 per username and 20 per IP per minute. Multi-worker deployments need a shared cache.
