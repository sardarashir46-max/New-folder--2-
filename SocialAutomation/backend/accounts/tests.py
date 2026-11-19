import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

class AuthenticationAPITests(TestCase):
    def setUp(self):
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

    def test_login_rejects_invalid_credentials(self):
        response = self.post_json(
            "/api/auth/login/", {"username": "morgan", "password": "wrong-password"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 401)

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

    def test_unauthenticated_channel_api_access_is_rejected(self):
        self.assertIn(self.client.get("/api/channels/").status_code, (401, 403))
