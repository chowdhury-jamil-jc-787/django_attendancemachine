
"""Tests for authentication, device sessions and password-change logout."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase, APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from .models import BlacklistedAccessToken, DeviceSession


User = get_user_model()


class DeviceSessionTests(APITestCase):
    """Exercise the real API endpoints with signed JWTs."""

    def setUp(self):
        self.username = "session_test_user"
        self.password = "OldStrongPassword123!"
        self.new_password = "NewStrongPassword456!"
        self.user = User.objects.create_user(
            username=self.username,
            email="session-test@example.com",
            password=self.password,
        )
        self.login_url = reverse("auth_login")
        self.dashboard_url = reverse("dashboard")
        self.sessions_url = reverse("auth_device_sessions")
        self.change_password_url = reverse("auth_change_password")

    def make_client(self, access_token=None):
        client = APIClient()
        if access_token:
            client.credentials(
                HTTP_AUTHORIZATION=f"Bearer {access_token}"
            )
        return client

    def login(self, device="Mozilla/5.0 (Windows NT 10.0) Chrome/120.0"):
        response = self.client.post(
            self.login_url,
            {"username": self.username, "password": self.password},
            format="json",
            HTTP_USER_AGENT=device,
            REMOTE_ADDR="192.0.2.10",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data

    def assert_token_rejected(self, access_token):
        response = self.make_client(access_token).get(self.dashboard_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def password_payload(self, **options):
        return {
            "old_password": self.password,
            "new_password": self.new_password,
            "confirm_password": self.new_password,
            **options,
        }

    def test_login_creates_separate_device_sessions(self):
        first = self.login("Mozilla/5.0 (Windows NT 10.0) Chrome/120.0")
        second = self.login("Mozilla/5.0 (iPhone) Safari/604.1")

        first_refresh = RefreshToken(first["refresh"])
        second_refresh = RefreshToken(second["refresh"])
        self.assertNotEqual(first_refresh["sid"], second_refresh["sid"])
        self.assertEqual(DeviceSession.objects.filter(user=self.user).count(), 2)
        first_session = DeviceSession.objects.get(id=first_refresh["sid"])
        self.assertEqual(str(first_session.id), first_refresh["sid"])
        self.assertEqual(first_session.device_name, "Chrome on Windows")
        self.assertEqual(first_session.ip_address, "192.0.2.10")
        self.assertEqual(first_refresh["sid"], first_refresh.access_token["sid"])
        self.assertEqual(first["status"], True)
        self.assertIn("user", first)
        self.assertIn("profile", first)

        self.assertEqual(
            self.make_client(first["access"]).get(self.dashboard_url).status_code,
            status.HTTP_200_OK,
        )
        self.assertEqual(
            self.make_client(second["access"]).get(self.dashboard_url).status_code,
            status.HTTP_200_OK,
        )

    def test_registration_returns_device_bound_tokens(self):
        response = self.client.post(
            reverse("auth_register"),
            {
                "username": "registered_session_user",
                "email": "registered-session@example.com",
                "password": "AnotherStrongPassword789!",
                "first_name": "Test",
                "last_name": "User",
                "emp_code": "TEST-SID-90812",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["status"])
        sid = RefreshToken(response.data["refresh"])["sid"]
        self.assertTrue(DeviceSession.objects.filter(id=sid).exists())
        self.assertEqual(
            self.make_client(response.data["access"]).get(
                self.dashboard_url
            ).status_code,
            status.HTTP_200_OK,
        )

    def test_wrong_credentials_do_not_create_session(self):
        response = self.client.post(
            self.login_url,
            {"username": self.username, "password": "incorrect"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(DeviceSession.objects.count(), 0)

    def test_list_sessions_marks_current_device_and_excludes_revoked(self):
        first = self.login()
        second = self.login("Mozilla/5.0 (iPhone) Safari/604.1")
        client = self.make_client(first["access"])

        response = client.get(self.sessions_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            sum(session["is_current"] for session in response.data["sessions"]),
            1,
        )
        self.assertEqual(
            {session["id"] for session in response.data["sessions"]},
            {RefreshToken(first["refresh"])["sid"],
             RefreshToken(second["refresh"])["sid"]},
        )

    def test_logout_specific_device_revokes_only_that_device(self):
        first = self.login()
        second = self.login("Mozilla/5.0 (iPhone) Safari/604.1")
        first_client = self.make_client(first["access"])
        second_id = RefreshToken(second["refresh"])["sid"]

        response = first_client.delete(
            reverse("auth_logout_device", kwargs={"session_id": second_id})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["logged_out_current_device"])
        self.assert_token_rejected(second["access"])
        self.assertEqual(
            first_client.get(self.dashboard_url).status_code,
            status.HTTP_200_OK,
        )

    def test_cannot_logout_another_users_device(self):
        owner = self.login()
        other_user = User.objects.create_user(
            username="another_user", password="AnotherPassword789!"
        )
        other_session = DeviceSession.objects.create(
            user=other_user,
            expires_at=timezone.now() + timedelta(days=1),
        )
        response = self.make_client(owner["access"]).delete(
            reverse("auth_logout_device", kwargs={"session_id": other_session.id})
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        other_session.refresh_from_db()
        self.assertIsNone(other_session.revoked_at)

    def test_logout_other_devices_keeps_current_session(self):
        current = self.login()
        other = self.login("Mozilla/5.0 (iPhone) Safari/604.1")
        current_client = self.make_client(current["access"])

        response = current_client.post(reverse("auth_logout_other_devices"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["revoked_sessions"], 1)
        self.assertEqual(
            current_client.get(self.dashboard_url).status_code,
            status.HTTP_200_OK,
        )
        self.assert_token_rejected(other["access"])

    def test_logout_all_devices_invalidates_every_access_token(self):
        first = self.login()
        second = self.login("Mozilla/5.0 (iPhone) Safari/604.1")
        response = self.make_client(first["access"]).post(
            reverse("auth_logout_all_devices")
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["revoked_sessions"], 2)
        self.assert_token_rejected(first["access"])
        self.assert_token_rejected(second["access"])

    def test_regular_logout_revokes_session_and_blacklists_access(self):
        tokens = self.login()
        response = self.make_client(tokens["access"]).post(
            reverse("auth_logout")
        )
        self.assertEqual(response.status_code, status.HTTP_205_RESET_CONTENT)
        self.assertEqual(response.data["message"], "Access token blacklisted")
        self.assertTrue(DeviceSession.objects.get(
            id=RefreshToken(tokens["refresh"])["sid"]
        ).revoked_at)
        self.assertTrue(BlacklistedAccessToken.objects.exists())
        self.assert_token_rejected(tokens["access"])

    def test_active_device_can_refresh_access_token(self):
        tokens = self.login()
        response = self.client.post(
            reverse("token_refresh"),
            {"refresh": tokens["refresh"]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)
        self.assertEqual(
            self.make_client(response.data["access"]).get(
                self.dashboard_url
            ).status_code,
            status.HTTP_200_OK,
        )

    def test_revoked_device_cannot_refresh_token(self):
        tokens = self.login()
        self.make_client(tokens["access"]).post(reverse("auth_logout_all_devices"))
        response = self.client.post(
            reverse("token_refresh"), {"refresh": tokens["refresh"]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_token_endpoint_creates_device_bound_tokens(self):
        response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": self.username, "password": self.password},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sid = RefreshToken(response.data["refresh"])["sid"]
        self.assertTrue(DeviceSession.objects.filter(id=sid, user=self.user).exists())
        self.assertEqual(
            self.make_client(response.data["access"]).get(
                self.dashboard_url
            ).status_code,
            status.HTTP_200_OK,
        )

    def test_refresh_token_cannot_be_used_as_access_token(self):
        tokens = self.login()
        self.assert_token_rejected(tokens["refresh"])

    def test_legacy_access_token_without_session_is_rejected(self):
        legacy_token = str(RefreshToken.for_user(self.user).access_token)
        self.assert_token_rejected(legacy_token)

    def test_password_change_without_logout_preserves_sessions(self):
        first = self.login()
        second = self.login("Mozilla/5.0 (iPhone) Safari/604.1")
        response = self.make_client(first["access"]).post(
            self.change_password_url,
            self.password_payload(),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["message"], "Password updated successfully.")
        self.assertEqual(response.data["revoked_sessions"], 0)
        self.assertEqual(
            self.make_client(second["access"]).get(self.dashboard_url).status_code,
            status.HTTP_200_OK,
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.new_password))

    def test_password_change_logout_other_devices(self):
        current = self.login()
        other = self.login("Mozilla/5.0 (iPhone) Safari/604.1")
        client = self.make_client(current["access"])
        response = client.post(
            self.change_password_url,
            self.password_payload(logout_other_devices=True),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["current_session_active"])
        self.assertEqual(response.data["revoked_sessions"], 1)
        self.assertEqual(client.get(self.dashboard_url).status_code, 200)
        self.assert_token_rejected(other["access"])

    def test_password_change_logout_all_devices(self):
        current = self.login()
        other = self.login("Mozilla/5.0 (iPhone) Safari/604.1")
        response = self.make_client(current["access"]).post(
            self.change_password_url,
            self.password_payload(logout_all_devices=True),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["current_session_active"])
        self.assertEqual(response.data["revoked_sessions"], 2)
        self.assert_token_rejected(current["access"])
        self.assert_token_rejected(other["access"])

    def test_password_change_rejects_wrong_old_password(self):
        tokens = self.login()
        response = self.make_client(tokens["access"]).post(
            self.change_password_url,
            {**self.password_payload(logout_all_devices=True),
             "old_password": "wrong-password"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIsNone(DeviceSession.objects.first().revoked_at)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_anonymous_device_list_requires_authentication(self):
        response = self.client.get(self.sessions_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
