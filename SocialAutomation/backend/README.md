# Backend

Django REST API for SocialAutomation.

## Initial setup

```bash
python -m venv venv
# Windows CMD: venv\\Scripts\\activate
pip install -r requirements.txt
```

If `backend/.env` does not already exist, copy `.env.example` to `.env`. Preserve any existing `.env` and add or update only the required keys. Set a random `DJANGO_SECRET_KEY`, `YOUTUBE_API_KEY` from Google Cloud Console, and the Facebook settings below. Enable **YouTube Data API v3** and restrict the API key to that API. Never commit `.env`.

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

## Facebook Pages connection

Facebook Login uses the official Meta authorization-code flow. Create a Meta developer app, enable Facebook Login, and add this exact valid OAuth redirect URI to its Facebook Login settings:

```text
http://localhost:8000/api/facebook/oauth/callback/
```

Set `FACEBOOK_APP_ID` and `FACEBOOK_APP_SECRET` from that app, `FACEBOOK_REDIRECT_URI` to the same callback, and `FACEBOOK_FRONTEND_URL` to the dashboard origin. Generate a private Fernet key for `FACEBOOK_TOKEN_ENCRYPTION_KEY` with:

```bash
py -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Keep the encryption key stable and secret: stored Facebook user and Page tokens cannot be decrypted without it. Do not put the Meta app secret or Fernet key in frontend variables or commit them. For deployment, configure an HTTPS callback and dashboard URL, secure cookies, exact CORS/CSRF origins, and keep the key in a secret manager.

The integration requests `pages_show_list`, `pages_read_engagement`, and `pages_manage_posts`. Meta must actually grant each permission: requesting a scope does not grant it. The app may need Advanced Access and Meta App Review for people outside app roles, and the user must have Page access with the `CREATE_CONTENT` task. The Pages screen reports missing permissions and Page task capability. Existing connections without all scopes need to reconnect. The OAuth state is single-use, token exchange stays server-side, granted scopes are stored, and user/Page tokens are Fernet-encrypted and excluded from API responses. A Page selection is a user preference, not proof of rights.

The official server-side video adapter posts to `/{page_id}/videos` on the configured Graph API version, using Meta's start/transfer/finish chunked upload with video bytes read from private storage. It does not publish a public media URL. A confirmed Meta video ID is required before recording success. Meta API versions, video limits and app-review requirements change; check the current Page Videos and Video API Publishing documentation before deployment. Configure `FACEBOOK_GRAPH_API_VERSION` to a supported version.

## Content library and publishing scheduler foundation

Run migrations with `py manage.py migrate`. Video files are stored under `SOCIALAUTOMATION_MEDIA_ROOT` (default `backend/uploads`) and are not served as public static files. The authenticated file endpoint checks ownership before streaming. `MEDIA_MAX_UPLOAD_MB` defaults to 500 MB. Uploads must have a supported extension and matching MP4/M4V, QuickTime MOV, WebM, Matroska MKV, or AVI container signature; the browser-supplied MIME type and filename are not trusted. This foundation checks the container signature but does not transcode, scan for malware, or verify rights. New uploads start as `pending_review`; an Owner/Authorized status is a user declaration recorded with a timestamp and account, not independent verification.

The API routes are:

- `GET/POST /api/library/media/`, `GET/PATCH/DELETE /api/library/media/{id}/`, `GET /api/library/media/{id}/file/`, `PATCH /api/library/media/{id}/rights/`.
- `GET/POST /api/publishing/posts/`, `GET/PATCH/DELETE /api/publishing/posts/{id}/`, and `POST /api/publishing/posts/{id}/cancel/`.
- `GET /api/publishing/history/` and `GET /api/dashboard/summary/`.

Creating a scheduled post requires ready media declared Owned or Authorized, a future timezone-aware date/time, and one or more available, selected Pages from the user's currently connected Facebook account. Per-Page targets and publishing attempts are stored separately. Edits are limited to pending posts; cancellation preserves the schedule and history. Deleting media or withdrawing its approval cancels pending/queued schedules that depend on it.

Celery Beat queues due posts using an atomic database claim, and workers record per-Page attempts. Publishing dispatch is **off by default**. Keep `FACEBOOK_PUBLISHING_ENABLED=False` until the Meta app, required access, encrypted Page tokens, private media storage, Redis and workers are configured. With publishing disabled, due posts stay Pending and no attempts are created. Enable it explicitly with `FACEBOOK_PUBLISHING_ENABLED=True`; `PUBLISHING_DISPATCH_ENABLED` can independently pause dispatch. Workers recheck rights, ownership, Page task and token. Temporary errors retry with exponential backoff; uncertain finish responses or stale processing targets become Review required so they are not automatically duplicated. Review Page activity before manually taking further action. There is no automatic retry control for review-required posts.

Start Redis and then run these from `backend/` in separate terminals:

```bash
py manage.py runserver
py -m celery -A config worker --pool=solo --loglevel=INFO
py -m celery -A config beat --loglevel=INFO
```

Tune `PUBLISHING_DISPATCH_INTERVAL_SECONDS`, `PUBLISHING_DISPATCH_STALE_SECONDS`, and `PUBLISHING_MAX_RETRIES` in `.env`. Keep the stale timeout above the maximum one-hour retry backoff. Uploaded media must meet the configured `MEDIA_MAX_UPLOAD_MB` limit and supported container checks; files must remain readable by the worker. The dashboard reports the server publishing switch and connection requirements. Meta publishing is not verified against a real Page by automated tests.

From `frontend/`, run `npm install` and `npm run dev`. The Media Library, Publishing Calendar, and Activity Logs use the authenticated API and retain the existing dashboard styling. The calendar shows scheduling and attempt outcomes; it does not send posts to Facebook.
