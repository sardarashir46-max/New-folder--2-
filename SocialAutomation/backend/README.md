# Backend

Django REST API for SocialAutomation.

## Initial setup

```bash
python -m venv venv
# Windows CMD: venv\Scripts\activate
pip install -r requirements.txt
django-admin startproject config .
python manage.py migrate
python manage.py runserver
```

The API will be developed in the next milestone. Keep credentials in environment variables and never commit tokens or secrets.
