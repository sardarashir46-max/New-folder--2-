# Backend

Django REST API for SocialAutomation.

## Initial setup

```bash
python -m venv venv
# Windows CMD: venv\\Scripts\\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env`, set a random `DJANGO_SECRET_KEY`, and set `YOUTUBE_API_KEY` from Google Cloud Console. Enable **YouTube Data API v3** and restrict the API key to that API. Never commit `.env`.

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

This milestone discovers public metadata only. It does not download video files or grant permission to reuse them. Unverified videos must not be sent to a publishing queue. Monitoring is currently a stored preference with manual refresh; background polling will be added with the scheduling worker milestone.

Login attempts are limited to 5 per username and 20 per IP per minute. Multi-worker deployments need a shared cache.
