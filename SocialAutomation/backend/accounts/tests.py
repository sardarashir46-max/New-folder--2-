import json

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import Client, TestCase


class AuthenticationAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client(enforce_csrf_checks=True)
        self.user = get_user_model().objects.create_user(
            username="morgan",
            email="morgan@example.com",
            password="A-long-unique-password-942!",
        )

    def csrf_headers(self):
        response = self.client.get("/api/auth/csrf/")
        self.assertEqual(response.status_code, 200)
        return {"HTTP_X_CSRFTOKEN": response.json()["csrfToken"]}

    def post_json(self, path, data):
        return self.client.post(
            path,
            data=json.dumps(data),
            content_type="application/json",
            **self.csrf_headers(),
        )

    def test_registration_creates_hashed_password_and_starts_session(self):
        response = self.post_json(
            "/api/auth/register/",
            {
                "username": "new-member",
                "email": "new@example.com",
                "password": "Another-long-password-375!",
                "password_confirm": "Another-long-password-375!",
            },
        )
        self.assertEqual(response.status_code, 201)
        user = get_user_model().objects.get(username="new-member")
        self.assertTrue(user.check_password("Another-long-password-375!"))
        self.assertNotIn("password", response.json()["user"])
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 200)

    def test_registration_rejects_weak_password(self):
        response = self.post_json(
            "/api/auth/register/",
            {
                "username": "weak-password-user",
                "email": "weak@example.com",
                "password": "password",
                "password_confirm": "password",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.json()["errors"])

    def test_registration_rejects_password_confirmation_mismatch(self):
        response = self.post_json(
            "/api/auth/register/",
            {
                "username": "mismatch-user",
                "email": "mismatch@example.com",
                "password": "Another-long-password-375!",
                "password_confirm": "Different-long-password-375!",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("password_confirm", response.json()["errors"])
        self.assertFalse(get_user_model().objects.filter(username="mismatch-user").exists())

    def test_registration_rejects_non_string_username(self):
        response = self.post_json(
            "/api/auth/register/",
            {
                "username": None,
                "email": "typed@example.com",
                "password": "Another-long-password-375!",
                "password_confirm": "Another-long-password-375!",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("username", response.json()["errors"])

    def test_registration_rejects_duplicate_username_case_insensitively(self):
        response = self.post_json(
            "/api/auth/register/",
            {
                "username": "MORGAN",
                "email": "different@example.com",
                "password": "Another-long-password-375!",
                "password_confirm": "Another-long-password-375!",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("username", response.json()["errors"])

    def test_registration_rejects_duplicate_email_case_insensitively(self):
        response = self.post_json(
            "/api/auth/register/",
            {
                "username": "different-user",
                "email": "MORGAN@example.com",
                "password": "Another-long-password-375!",
                "password_confirm": "Another-long-password-375!",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.json()["errors"])

    def test_database_rejects_duplicate_email_case_insensitively(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                get_user_model().objects.create_user(
                    username="duplicate-email",
                    email="MORGAN@example.com",
                    password="A-long-unique-password-942!",
                )

    def test_database_allows_multiple_blank_legacy_emails(self):
        user_model = get_user_model()
        user_model.objects.create_user(username="blank-email-one", password="password")
        user_model.objects.create_user(username="blank-email-two", password="password")
        self.assertEqual(user_model.objects.filter(email="").count(), 2)

    def test_registration_response_never_contains_plaintext_password(self):
        plain_password = "Another-long-password-375!"
        response = self.post_json(
            "/api/auth/register/",
            {
                "username": "response-check",
                "email": "response@example.com",
                "password": plain_password,
                "password_confirm": plain_password,
            },
        )
        user = get_user_model().objects.get(username="response-check")
        self.assertNotEqual(user.password, plain_password)
        self.assertNotIn(plain_password, response.content.decode())

    def test_csrf_cookie_is_http_only_and_login_requires_csrf(self):
        response = self.client.get("/api/auth/csrf/")
        self.assertTrue(response.cookies["csrftoken"]["httponly"])
        login_response = self.client.post(
            "/api/auth/login/",
            data=json.dumps({"username": "morgan", "password": "A-long-unique-password-942!"}),
            content_type="application/json",
        )
        self.assertEqual(login_response.status_code, 403)

    def test_registration_requires_csrf_token(self):
        response = self.client.post(
            "/api/auth/register/", data='{"username":"blocked"}', content_type="application/json"
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(get_user_model().objects.filter(username="blocked").exists())

    def test_login_creates_session(self):
        response = self.post_json(
            "/api/auth/login/",
            {"username": "morgan", "password": "A-long-unique-password-942!"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me/").json()["user"]["username"], "morgan")
        self.assertTrue(self.client.cookies["sessionid"]["httponly"])
        self.assertEqual(self.client.cookies["sessionid"]["samesite"], "Lax")

    def test_session_persists_for_a_new_client_using_its_cookie(self):
        self.post_json(
            "/api/auth/login/",
            {"username": "morgan", "password": "A-long-unique-password-942!"},
        )
        session_cookie = self.client.cookies["sessionid"].value
        refreshed_client = Client()
        refreshed_client.cookies["sessionid"] = session_cookie
        response = refreshed_client.get("/api/auth/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["username"], "morgan")

    def test_expired_session_no_longer_authenticates(self):
        self.client.force_login(self.user)
        session = self.client.session
        session.set_expiry(-1)
        session.save()
        response = self.client.get("/api/auth/me/")
        self.assertEqual(response.status_code, 401)

    def test_login_rejects_invalid_credentials(self):
        response = self.post_json(
            "/api/auth/login/", {"username": "morgan", "password": "wrong-password"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)

    def test_login_throttles_repeated_attempts_with_generic_message(self):
        for _ in range(5):
            response = self.post_json(
                "/api/auth/login/", {"username": "morgan", "password": "wrong-password"}
            )
            self.assertEqual(response.status_code, 400)

        response = self.post_json(
            "/api/auth/login/", {"username": "morgan", "password": "wrong-password"}
        )
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)
        self.assertIn("Too many sign-in attempts", response.json()["detail"])
        self.assertNotIn("morgan", response.json()["detail"])

    def test_logout_ends_session(self):
        self.client.force_login(self.user)
        response = self.post_json("/api/auth/logout/", {})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)

    def test_logout_requires_csrf_token(self):
        self.client.force_login(self.user)
        response = self.client.post("/api/auth/logout/", data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 403)

    def test_current_user_requires_authentication(self):
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)

    def test_current_user_returns_profile_without_credentials(self):
        self.client.force_login(self.user)
        response = self.client.get("/api/auth/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()["user"]), {"id", "username", "email"})

    def test_unauthenticated_channel_api_access_is_rejected(self):
        self.assertIn(self.client.get("/api/channels/").status_code, (401, 403))
