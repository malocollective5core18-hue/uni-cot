from django.test import TestCase, override_settings

from customers.models import TenantSubscription
from service.models import OwnerUser


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)
class OwnerSignupFlowTests(TestCase):
    def test_owner_signup_redirects_to_owner_dashboard_in_path_mode(self):
        response = self.client.post(
            "/service/register/",
            {
                "program_name": "BCIT",
                "email": "owner@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Owner dashboard")

        owner = OwnerUser.objects.get(email="owner@example.com")
        tenant = owner.tenant

        self.assertTrue(response.redirect_chain)
        self.assertEqual(
            response.redirect_chain[-1][0],
            f"/t/{tenant.subdomain}/{tenant.id}/owner-dashboard/",
        )
        self.assertTrue(TenantSubscription.objects.filter(tenant=tenant, plan="trial").exists())
