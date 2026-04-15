import json

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from core.models import User
from service.models import OwnerUser


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)
class FounderLoginTests(TestCase):
    def test_founder_can_log_in_with_email_and_password(self):
        founder = get_user_model().objects.create_user(
            username="founder",
            email="founder@example.com",
            password="secret123",
            is_staff=True,
        )

        response = self.client.post(
            "/founder/login/",
            {
                "action": "founder_login",
                "email": founder.email,
                "password": "secret123",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Founder Control")


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)
class TenantSystemIsolationTests(TestCase):
    def setUp(self):
        self.client.post(
            "/service/register/",
            {
                "program_name": "BCIT",
                "email": "owner-api@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
            },
            follow=True,
        )
        self.owner = OwnerUser.objects.get(email="owner-api@example.com")
        self.tenant = self.owner.tenant

    def test_tenant_users_api_requires_owner_session(self):
        self.client.post(f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/logout/", follow=True)

        response = self.client.get(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/users/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 403)
        self.assertJSONEqual(
            response.content,
            {"success": False, "error": "Owner login required for this tenant system"},
        )

    def test_tenant_users_api_returns_only_current_owner_records(self):
        other_owner = OwnerUser.objects.create(
            email="owner-other@example.com",
            program_name="Other",
            password="hash",
            is_owner=True,
            is_active=True,
        )
        User.objects.create(full_name="Owner One User", registration_number="BCIT-001", created_by=self.owner.id)
        User.objects.create(full_name="Other User", registration_number="OTHER-001", created_by=other_owner.id)

        response = self.client.get(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/users/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(len(payload["data"]), 1)
        self.assertEqual(payload["data"][0]["registration_number"], "BCIT-001")
