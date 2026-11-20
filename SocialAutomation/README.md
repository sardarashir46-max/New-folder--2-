# SocialAutomation

A multi-channel content operations dashboard for managing authorized media, connected YouTube channels, Facebook Pages, publishing schedules, and activity.

## Project status

**Current milestone — Content library and scheduler foundation**

- [x] Initial repository structure
- [x] React dashboard shell and navigation
- [x] Overview, channels, Pages, media library, calendar, and activity views
- [x] Django REST API integration for authentication and YouTube metadata
- [x] Official Facebook Login and owner-scoped Page selection
- [x] Background YouTube metadata monitoring with Celery and Redis
- [x] Private user media library, rights declarations, and scheduled-post workflow foundation
- [ ] Publishing (not implemented; requires a separate rights and permissions milestone)

## Architecture

- **Frontend:** React + Vite
- **Backend:** Django + Django REST Framework
- **Database:** SQLite for local development
- **Background jobs:** Celery + Redis for scheduled YouTube metadata checks
- **Facebook:** Meta Graph API OAuth, authorized Page discovery and selection
- **Publishing:** Not implemented; requires required permissions, review, and rights checks
- **Media:** User-owned or properly licensed source files

## Local development

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Backend

```bash
cd backend
python -m venv venv
# Windows CMD:
venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver
```

## Important

This application is designed to publish only media the account owner has the right to distribute. YouTube metadata discovery is separate from obtaining a video file; do not download or re-upload YouTube audiovisual content without authorization.

Facebook OAuth app setup, environment variables, and local run instructions are documented in [backend/README.md](backend/README.md#facebook-pages-connection). The Facebook integration only reads the authorized Page list and stores a selection preference. It does not publish content or verify rights.

Content Library uploads are private and served through an authenticated endpoint. New items need a user rights declaration before scheduling. The scheduler records per-Page attempt states but the Meta publishing adapter is deliberately disabled; due posts will fail safely without sending content. Setup and API details are in [backend/README.md](backend/README.md#content-library-and-publishing-scheduler-foundation).
