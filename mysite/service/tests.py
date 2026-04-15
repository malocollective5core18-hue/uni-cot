from django.test import TestCase, override_settings

from customers.models import TenantSubscription
from core.models import User as CoreUser
from service.models import Comment, Member, OwnerUser


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
            f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/owner-dashboard/",
        )

    def test_service_welcome_uses_real_review_summary(self):
        self.client.post(
            "/service/register/",
            {
                "program_name": "BCIT",
                "email": "owner-summary@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
            },
            follow=True,
        )
        owner = OwnerUser.objects.get(email="owner-summary@example.com")
        tenant = owner.tenant

        member = Member.objects.create(
            owner=owner,
            reg_number="BCIT-SUM-1",
            program_name="BCIT",
            password="hashed",
            is_active=True,
        )
        Comment.objects.create(member=member, owner=owner, content="Great", rating=5, status="approved")
        Comment.objects.create(member=member, owner=owner, content="Good", rating=3, status="approved")

        self.client.post(f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/logout/", follow=True)
        response = self.client.get(f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/service/welcome/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "4.0")
        self.assertContains(response, "2 verified ratings")

    def test_review_reaction_persists_across_requests(self):
        self.client.post(
            "/service/register/",
            {
                "program_name": "BCIT",
                "email": "owner-reaction@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
            },
            follow=True,
        )
        owner = OwnerUser.objects.get(email="owner-reaction@example.com")
        tenant = owner.tenant

        CoreUser.objects.create(
            full_name="Member One",
            registration_number="BCIT-R1",
            created_by=owner.id,
            is_active=True,
        )
        self.client.post(
            f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/register-member/",
            {
                "reg_number": "BCIT-R1",
                "program_name": "BCIT",
                "password": "memberpass",
                "confirm_password": "memberpass",
            },
            follow=True,
        )
        self.client.post(f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/logout/", follow=True)
        self.client.post(
            f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/login/",
            {"login_identifier": "BCIT-R1", "password": "memberpass"},
            follow=True,
        )

        member = Member.objects.get(reg_number="BCIT-R1")
        comment = Comment.objects.create(member=member, owner=owner, content="Helpful", rating=5, status="approved")

        response = self.client.post(
            f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/comment/{comment.id}/like/",
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        comment.refresh_from_db()
        self.assertEqual(comment.likes, 1)
        self.assertEqual(comment.dislikes, 0)

    def test_member_reply_is_persisted(self):
        self.client.post(
            "/service/register/",
            {
                "program_name": "BCIT",
                "email": "owner-reply@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
            },
            follow=True,
        )
        owner = OwnerUser.objects.get(email="owner-reply@example.com")
        tenant = owner.tenant

        CoreUser.objects.create(
            full_name="Member Reply",
            registration_number="BCIT-R2",
            created_by=owner.id,
            is_active=True,
        )
        self.client.post(
            f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/register-member/",
            {
                "reg_number": "BCIT-R2",
                "program_name": "BCIT",
                "password": "memberpass",
                "confirm_password": "memberpass",
            },
            follow=True,
        )
        self.client.post(f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/logout/", follow=True)
        self.client.post(
            f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/login/",
            {"login_identifier": "BCIT-R2", "password": "memberpass"},
            follow=True,
        )

        member = Member.objects.get(reg_number="BCIT-R2")
        comment = Comment.objects.create(member=member, owner=owner, content="Original", rating=4, status="approved")

        response = self.client.post(
            f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/comment/{comment.id}/reply/",
            {"content": "Real reply"},
        )

        self.assertEqual(response.status_code, 200)
        comment.refresh_from_db()
        self.assertEqual(comment.replies.count(), 1)
        self.assertEqual(comment.replies.first().content, "Real reply")

    def test_member_login_uses_registration_number_and_owner_system_record(self):
        signup_response = self.client.post(
            "/service/register/",
            {
                "program_name": "BCIT",
                "email": "owner-member@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
            },
            follow=True,
        )
        self.assertEqual(signup_response.status_code, 200)

        owner = OwnerUser.objects.get(email="owner-member@example.com")
        tenant = owner.tenant

        CoreUser.objects.create(
            full_name="Member One",
            registration_number="BCIT-001",
            created_by=owner.id,
            is_active=True,
        )

        register_response = self.client.post(
            f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/register-member/",
            {
                "reg_number": "BCIT-001",
                "program_name": "BCIT",
                "password": "memberpass",
                "confirm_password": "memberpass",
            },
            follow=True,
        )
        self.assertEqual(register_response.status_code, 200)
        self.assertContains(register_response, "registered successfully")

        self.client.post(f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/logout/", follow=True)

        login_response = self.client.post(
            f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/login/",
            {
                "login_identifier": "BCIT-001",
                "password": "memberpass",
            },
            follow=True,
        )

        self.assertEqual(login_response.status_code, 200)
        self.assertContains(login_response, "Member Dashboard")
        self.assertTrue(login_response.redirect_chain)
        self.assertEqual(
            login_response.redirect_chain[-1][0],
            f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/member-dashboard/",
        )

    def test_owner_cannot_register_member_without_owner_system_record(self):
        signup_response = self.client.post(
            "/service/register/",
            {
                "program_name": "BCIT",
                "email": "owner-no-core@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
            },
            follow=True,
        )
        self.assertEqual(signup_response.status_code, 200)

        owner = OwnerUser.objects.get(email="owner-no-core@example.com")
        tenant = owner.tenant

        response = self.client.post(
            f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/register-member/",
            {
                "reg_number": "BCIT-404",
                "program_name": "BCIT",
                "password": "memberpass",
                "confirm_password": "memberpass",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "must already exist in your main system records")

    def test_owner_system_demo_renders_on_tenant_path(self):
        signup_response = self.client.post(
            "/service/register/",
            {
                "program_name": "BCIT",
                "email": "owner-system@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
            },
            follow=True,
        )
        self.assertEqual(signup_response.status_code, 200)

        owner = OwnerUser.objects.get(email="owner-system@example.com")
        tenant = owner.tenant

        response = self.client.get(
            f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/system/",
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome page")

    def test_tenant_system_demo_is_public_for_tenant_members(self):
        signup_response = self.client.post(
            "/service/register/",
            {
                "program_name": "BCIT",
                "email": "owner-system-guard@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
            },
            follow=True,
        )
        self.assertEqual(signup_response.status_code, 200)

        owner = OwnerUser.objects.get(email="owner-system-guard@example.com")
        tenant = owner.tenant

        self.client.post(f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/logout/", follow=True)

        response = self.client.get(
            f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/system/",
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Welcome page")

    def test_legacy_tenant_path_redirects_to_keyed_path(self):
        signup_response = self.client.post(
            "/service/register/",
            {
                "program_name": "BCIT",
                "email": "owner-legacy@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
            },
            follow=True,
        )
        self.assertEqual(signup_response.status_code, 200)

        owner = OwnerUser.objects.get(email="owner-legacy@example.com")
        tenant = owner.tenant

        response = self.client.get(
            f"/t/{tenant.subdomain}/{tenant.id}/system/",
            follow=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response["Location"],
            f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/system/",
        )
        self.assertTrue(TenantSubscription.objects.filter(tenant=tenant, plan="trial").exists())

    def test_owner_can_log_in_from_public_welcome_with_email_and_password(self):
        signup_response = self.client.post(
            "/service/register/",
            {
                "program_name": "BCIT",
                "email": "owner-login@example.com",
                "password": "secret123",
                "confirm_password": "secret123",
            },
            follow=True,
        )
        self.assertEqual(signup_response.status_code, 200)

        self.client.post("/service/logout/", follow=True)

        response = self.client.post(
            "/service/login/",
            {
                "login_identifier": "owner-login@example.com",
                "password": "secret123",
            },
            follow=True,
        )

        owner = OwnerUser.objects.get(email="owner-login@example.com")
        tenant = owner.tenant

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Owner dashboard")
        self.assertTrue(response.redirect_chain)
        self.assertEqual(
            response.redirect_chain[-1][0],
            f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/owner-dashboard/",
        )
