import json
from math import ceil

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from .throttles import LoginIPThrottle, LoginUsernameThrottle


def _login_throttle_response(request, username):
    request.login_throttle_username = username.casefold()
    waits = []
    for throttle_class in (LoginUsernameThrottle, LoginIPThrottle):
        throttle = throttle_class()
        if not throttle.allow_request(request, view=None):
            waits.append(throttle.wait() or 60)

    if not waits:
        return None

    response = JsonResponse(
        {"detail": "Too many sign-in attempts. Please wait a little while and try again."},
        status=429,
    )
    response["Retry-After"] = str(ceil(max(waits)))
    return response


def _user_data(user):
    """Return only public profile fields, never password or session data."""
    return {"id": user.pk, "username": user.get_username(), "email": user.email}


def _request_data(request):
    try:
        return json.loads(request.body or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


@ensure_csrf_cookie
@require_GET
def csrf_cookie(request):
    return JsonResponse({"csrfToken": get_token(request)})


@csrf_protect
@require_POST
def register(request):
    data = _request_data(request)
    if not isinstance(data, dict):
        return JsonResponse({"detail": "Send registration details as JSON."}, status=400)

    username_value = data.get("username", "")
    email_value = data.get("email", "")
    username = username_value.strip() if isinstance(username_value, str) else ""
    email = email_value.strip() if isinstance(email_value, str) else ""
    password = data.get("password", "")
    password_confirm = data.get("password_confirm", "")
    errors = {}

    if not username:
        errors["username"] = ["Enter a username."]
    if not email or "@" not in email:
        errors["email"] = ["Enter a valid email address."]
    if not isinstance(password, str) or not password:
        errors["password"] = ["Enter a password."]
    if password != password_confirm:
        errors["password_confirm"] = ["The passwords do not match."]
    if errors:
        return JsonResponse({"errors": errors}, status=400)

    User = get_user_model()
    if User.objects.filter(username__iexact=username).exists():
        return JsonResponse({"errors": {"username": ["That username is already in use."]}}, status=400)
    if User.objects.filter(email__iexact=email).exists():
        return JsonResponse({"errors": {"email": ["That email address is already in use."]}}, status=400)

    user = User(username=username, email=email)
    try:
        user.full_clean(exclude=["password"])
    except ValidationError as error:
        for field, messages in error.message_dict.items():
            errors[field] = messages
    if errors:
        return JsonResponse({"errors": errors}, status=400)

    try:
        validate_password(password, user=user)
    except ValidationError as error:
        return JsonResponse({"errors": {"password": error.messages}}, status=400)

    try:
        with transaction.atomic():
            user = User.objects.create_user(username=username, email=email, password=password)
    except IntegrityError:
        # The database constraint is the final guard if requests race past the checks above.
        if User.objects.filter(email__iexact=email).exists():
            return JsonResponse({"errors": {"email": ["That email address is already in use."]}}, status=400)
        if User.objects.filter(username__iexact=username).exists():
            return JsonResponse({"errors": {"username": ["That username is already in use."]}}, status=400)
        raise

    login(request, user)
    return JsonResponse({"user": _user_data(user)}, status=201)


@csrf_protect
@require_POST
def login_view(request):
    data = _request_data(request)
    if not isinstance(data, dict):
        return JsonResponse({"detail": "Send login details as JSON."}, status=400)

    username_value = data.get("username", "")
    username = username_value.strip() if isinstance(username_value, str) else ""
    password = data.get("password", "")
    if not username or not isinstance(password, str):
        return JsonResponse({"detail": "Enter your username and password."}, status=400)

    throttle_response = _login_throttle_response(request, username)
    if throttle_response:
        return throttle_response

    user = authenticate(request, username=username, password=password)
    if user is None:
        return JsonResponse({"detail": "Username or password is incorrect."}, status=400)

    login(request, user)
    return JsonResponse({"user": _user_data(user)})


@csrf_protect
@require_POST
def logout_view(request):
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "You are not signed in."}, status=401)
    logout(request)
    return JsonResponse({"detail": "Signed out successfully."})


@require_GET
def current_user(request):
    if not request.user.is_authenticated:
        return JsonResponse({"detail": "Authentication is required."}, status=401)
    return JsonResponse({"user": _user_data(request.user)})
