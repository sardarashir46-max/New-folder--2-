# Backend

Django REST API for SocialAutomation.

## Initial setup

```bash
python -m venv venv
# Windows CMD: venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and replace `DJANGO_SECRET_KEY` with a long random value. The `.env` file is ignored by Git and must not be committed. Then run:

```bash
python manage.py migrate
python manage.py runserver
```

The authentication API is available under `/api/auth/`. Registration and login use Django sessions; unsafe requests require the CSRF cookie and `X-CSRFToken` header. For a cross-site production frontend, set both origin lists to the exact frontend origin and use `DJANGO_SECURE_COOKIES=True` with `DJANGO_COOKIE_SAMESITE=None` over HTTPS. Keep credentials in environment variables and never commit tokens or secrets.

Login attempts are limited to 5 per username and 20 per IP address per minute. The local default uses Django's per-process memory cache. Multi-worker deployments must configure a shared cache backend so all workers enforce the same limits.
