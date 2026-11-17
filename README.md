# SocialAutomation

A multi-channel content operations dashboard for managing authorized media, connected YouTube channels, Facebook Pages, publishing schedules, and activity.

## Project status

**Phase 1 — Dashboard foundation**

- [x] Initial repository structure
- [x] React dashboard shell and navigation
- [x] Overview, channels, Pages, media library, calendar, and activity views
- [ ] Django REST API integration
- [ ] OAuth integrations
- [ ] Background scheduling and publishing

## Architecture

- **Frontend:** React + Vite
- **Backend:** Django + Django REST Framework
- **Database:** PostgreSQL (planned)
- **Background jobs:** Celery + Redis (planned)
- **Publishing:** Meta Graph API, subject to required permissions and platform policies
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
