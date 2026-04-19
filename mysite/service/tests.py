from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.contrib.auth.hashers import make_password

from customers.models import get_public_tenant_domain
from service.models import OwnerUser, Member, Comment


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)
class OwnerSignupTests(TestCase):
    @patch("service.views.create_owner_tenant", side_effect=RuntimeError("boom"))
    def test_register_view_rolls_back_owner_when_tenant_creation_fails(self, mocked_create_owner_tenant):
        response = self.client.post(
            reverse("service:register"),
            {
                "program_name": "Computer Science",
                "email": "owner@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(OwnerUser.objects.count(), 0)
        self.assertContains(
            response,
            "Registration could not be completed right now. Please try again in a moment.",
        )
        mocked_create_owner_tenant.assert_called_once()

    @patch("service.views.create_owner_tenant", side_effect=RuntimeError("boom"))
    def test_api_create_tenant_rolls_back_owner_when_tenant_creation_fails(self, mocked_create_owner_tenant):
        response = self.client.post(
            reverse("service:api_create_tenant"),
            data='{"program_name":"Computer Science","email":"owner@example.com","password":"secret123"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 500)
        self.assertJSONEqual(
            response.content,
            {"error": "Tenant registration failed. Please try again later."},
        )
        self.assertEqual(OwnerUser.objects.count(), 0)
        mocked_create_owner_tenant.assert_called_once()


class TenantDomainConfigTests(TestCase):
    @patch.dict(
        "os.environ",
        {"PUBLIC_TENANT_DOMAIN": "https://uni-cot-1-0ujy.onrender.com"},
        clear=False,
    )
    def test_public_tenant_domain_strips_protocol(self):
        self.assertEqual(get_public_tenant_domain(), "uni-cot-1-0ujy.onrender.com")


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)
class CrossDeviceLoginTests(TestCase):
    def test_owner_can_log_in_from_public_service_page(self):
        owner = OwnerUser.objects.create(
            email="owner@example.com",
            program_name="BCIT",
            password=make_password("secret123"),
            is_owner=True,
            is_active=True,
        )

        response = self.client.post(
            reverse("service:login"),
            {
                "login_identifier": "owner@example.com",
                "password": "secret123",
            },
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.url.endswith(reverse("service:owner_dashboard")),
            response.url,
        )
        session = self.client.session
        self.assertEqual(session["service_user"]["owner_id"], owner.id)
        self.assertEqual(session["service_user"]["user_type"], "owner")

    def test_member_login_still_requires_owner_domain(self):
        owner = OwnerUser.objects.create(
            email="owner@example.com",
            program_name="BCIT",
            password=make_password("secret123"),
            is_owner=True,
            is_active=True,
        )
        Member.objects.create(
            owner=owner,
            reg_number="BCIT-001",
            program_name="BCIT",
            password=make_password("member123"),
            is_active=True,
        )

        response = self.client.post(
            reverse("service:login"),
            {
                "login_identifier": "BCIT-001",
                "password": "member123",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please log in from your owner domain.")


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)
class CsrfApiFlowTests(TestCase):
    def test_welcome_sets_csrf_cookie_and_owner_api_post_accepts_it(self):
        client = Client(enforce_csrf_checks=True)

        welcome_response = client.get("/service/welcome/")
        self.assertEqual(welcome_response.status_code, 200)
        self.assertIn("csrftoken", client.cookies)

        csrf_token = client.cookies["csrftoken"].value

        register_response = client.post(
            reverse("service:register"),
            {
                "program_name": "BCIT",
                "email": "csrf-owner@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
            },
            HTTP_X_CSRFTOKEN=csrf_token,
            follow=False,
        )

        self.assertEqual(register_response.status_code, 302)

        csrf_token = client.cookies["csrftoken"].value
        api_response = client.post(
            "/api/users/",
            data='{"full_name":"Test Member","registration_number":"BCIT-001"}',
            content_type="application/json",
            HTTP_X_CSRFTOKEN=csrf_token,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(api_response.status_code, 201)

    def test_api_post_without_csrf_returns_json_403(self):
        client = Client(enforce_csrf_checks=True)

        response = client.post(
            "/api/users/",
            data='{"full_name":"No Token","registration_number":"BCIT-999"}',
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 403)
        self.assertJSONEqual(
            response.content,
            {
                "success": False,
                "error": "CSRF verification failed.",
                "reason": response.json()["reason"],
            },
        )


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)
class ReplyCommentTests(TestCase):
    def test_member_can_reply_to_approved_comment(self):
        owner = OwnerUser.objects.create(
            email="owner@example.com",
            program_name="BCIT",
            password=make_password("secret123"),
            is_owner=True,
            is_active=True,
        )
        member = Member.objects.create(
            owner=owner,
            reg_number="BCIT-001",
            program_name="BCIT",
            password=make_password("member123"),
            is_active=True,
        )
        comment = Comment.objects.create(
            member=member,
            owner=owner,
            content="Great service",
            rating=5,
            status="approved",
        )

        session = self.client.session
        session["service_user"] = {
            "user_type": "member",
            "member_id": member.id,
            "owner_id": owner.id,
        }
        session.save()

        response = self.client.post(
            reverse("service:reply_comment", args=[comment.id]),
            {"content": "Thank you"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(comment.replies.filter(content="Thank you", member=member).exists())

    def test_member_can_like_approved_comment(self):
        owner = OwnerUser.objects.create(
            email="owner2@example.com",
            program_name="BCIT",
            password=make_password("secret123"),
            is_owner=True,
            is_active=True,
        )
        member = Member.objects.create(
            owner=owner,
            reg_number="BCIT-002",
            program_name="BCIT",
            password=make_password("member123"),
            is_active=True,
        )
        comment = Comment.objects.create(
            member=member,
            owner=owner,
            content="Great service",
            rating=5,
            status="approved",
        )

        session = self.client.session
        session["service_user"] = {
            "user_type": "member",
            "member_id": member.id,
            "owner_id": owner.id,
        }
        session.save()

        response = self.client.post(
            reverse("service:react_comment", args=[comment.id, "like"]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        comment.refresh_from_db()
        self.assertEqual(comment.likes, 1)
