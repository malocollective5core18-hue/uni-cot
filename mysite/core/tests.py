import json
import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError
from django.contrib.auth.hashers import make_password
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.template.loader import render_to_string
from django.urls import reverse

from core import views
from core.models import ExternalTable, ExternalTableRecord, Property, User, UserGroup, UserGroupMember
from customers.models import CRTenant, TenantDashboardMetric
from service.models import OwnerUser
from mysite.mysite.rate_limit import is_rate_limited
from mysite.tests.helpers import CacheIsolationMixin


class SessionConfigurationTests(SimpleTestCase):
    def test_sessions_are_cache_backed_and_not_saved_on_every_request(self):
        self.assertEqual(settings.SESSION_ENGINE, 'django.contrib.sessions.backends.cache')
        self.assertEqual(settings.SESSION_CACHE_ALIAS, 'default')
        self.assertFalse(settings.SESSION_SAVE_EVERY_REQUEST)
        self.assertEqual(settings.SESSION_COOKIE_AGE, 60 * 60 * 24 * 7)


@override_settings(
    STORAGES={
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    },
)
class CompanyShowcaseTemplateTests(TestCase):
    """The public showcase is static and must not add render-time queries."""

    external_urls = (
        "https://lomi-apple.onrender.com",
        "https://speakora-ghfw.onrender.com/",
        "https://stanslau2025.github.io/DFIX_DGXcore",
        "https://34.35.47.111:3000",
        "https://www.mauzosheetai.co.tz/",
        "https://zima.co.tz/",
    )

    def _assert_showcase(self, template_name):
        with self.assertNumQueries(0):
            rendered = render_to_string(template_name, {
                "comments": [],
                "review_summary": {},
                "tenant_base_path": "",
            })

        vision_position = rendered.index('id="vision-projects"')
        collaborations_position = rendered.index('id="collaborations"')
        blog_position = rendered.index('id="blog"')
        self.assertLess(vision_position, collaborations_position)
        self.assertLess(collaborations_position, blog_position)

        for url in self.external_urls:
            self.assertRegex(
                rendered,
                rf'href="{re.escape(url)}"[^>]*target="_blank"[^>]*rel="noopener noreferrer"',
            )
        self.assertIn('BLACKSCIENCE Technologies', rendered)

    def test_index_showcase_renders_before_blog_without_queries(self):
        self._assert_showcase("index.html")

    def test_welcome_showcase_renders_before_blog_without_queries(self):
        self._assert_showcase("welcome.html")


class HealthEndpointTests(CacheIsolationMixin, TestCase):
    def test_healthz_makes_no_database_queries_or_session_cookie(self):
        with self.assertNumQueries(0):
            response = self.client.get('/healthz/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'ok')
        self.assertNotIn('sessionid', response.cookies)

    def test_readyz_checks_database_and_cache_without_session_cookie(self):
        # django-tenants activates the public schema before readyz performs
        # its single database probe.
        with self.assertNumQueries(2):
            response = self.client.get('/readyz/')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ready'])
        self.assertNotIn('sessionid', response.cookies)

    @override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "health-tests"}})
    def test_readyz_reports_unhealthy_when_cache_probe_fails(self):
        with patch('mysite.health.cache.get', return_value=None):
            response = self.client.get('/readyz/')

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()['ready'])


class ProvisionTickEndpointTests(CacheIsolationMixin, SimpleTestCase):
    @patch.dict('os.environ', {'PROVISION_TICK_ALLOW_QUERY_TOKEN': 'false'}, clear=False)
    def test_provision_tick_is_404_when_disabled(self):
        response = self.client.get('/internal/provision-tick/?token=secret')

        self.assertEqual(response.status_code, 404)

    @patch.dict('os.environ', {'PROVISION_TICK_ALLOW_QUERY_TOKEN': 'true', 'PROVISION_TICK_TOKEN': 'secret'}, clear=False)
    @patch('customers.provisioning.start_provisioner')
    @patch('customers.provisioning.kick_provisioner')
    def test_provision_tick_accepts_valid_token(self, kick_mock, start_mock):
        response = self.client.get('/internal/provision-tick/?token=secret')

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()['accepted'])
        start_mock.assert_called_once()
        kick_mock.assert_called_once()

    @patch.dict('os.environ', {'PROVISION_TICK_ALLOW_QUERY_TOKEN': 'true', 'PROVISION_TICK_TOKEN': 'secret'}, clear=False)
    def test_provision_tick_rejects_invalid_token(self):
        response = self.client.get('/internal/provision-tick/?token=wrong')

        self.assertEqual(response.status_code, 401)


class CachedJsonResponseTests(SimpleTestCase):
    def test_matching_etag_returns_not_modified_without_building_payload(self):
        factory = RequestFactory()
        first = views._cached_json_response(
            factory.get('/api/properties/'), 'etag-test', lambda: {'success': True}
        )
        etag = first['ETag']
        response = views._cached_json_response(
            factory.get('/api/properties/', HTTP_IF_NONE_MATCH=etag),
            'etag-test',
            lambda: self.fail('payload builder must not run for a matching ETag'),
        )

        self.assertEqual(response.status_code, 304)
        self.assertEqual(response['ETag'], etag)
        cache.clear()


class PaginationTests(SimpleTestCase):
    def test_page_size_is_capped_at_one_hundred(self):
        request = RequestFactory().get('/api/users/?page_size=1000')

        page, meta = views._paginate_queryset(request, list(range(101)))

        self.assertEqual(len(page.object_list), 100)
        self.assertEqual(meta['page_size'], 100)
        self.assertEqual(meta['total_pages'], 2)


class ApiErrorResponseTests(SimpleTestCase):
    def test_unexpected_api_error_does_not_include_exception_details(self):
        response = views._unexpected_api_error()

        self.assertEqual(response.status_code, 500)
        self.assertJSONEqual(
            response.content,
            {'success': False, 'error': 'Unable to complete this request. Please try again later.'},
        )


class SharedRateLimitTests(CacheIsolationMixin, SimpleTestCase):
    def test_limit_is_not_stored_in_the_session(self):
        request = RequestFactory().post('/', REMOTE_ADDR='203.0.113.10')

        self.assertFalse(is_rate_limited(request, 'test', limit=2, window_seconds=60, account='User@Example.com'))
        self.assertFalse(is_rate_limited(request, 'test', limit=2, window_seconds=60, account='user@example.com'))
        self.assertTrue(is_rate_limited(request, 'test', limit=2, window_seconds=60, account='user@example.com'))
        self.assertFalse(hasattr(request, 'session'))

@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)
class FounderLoginTests(CacheIsolationMixin, TestCase):
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


class FounderDashboardQueryTests(CacheIsolationMixin, TestCase):
    def test_tenant_page_uses_a_bounded_number_of_public_queries(self):
        for index in range(26):
            owner = OwnerUser.objects.create(
                email=f"owner-{index}@example.com",
                program_name=f"Program {index}",
                password="hashed",
            )
            tenant = CRTenant.objects.create(
                name=owner.program_name,
                schema_name=f"program_{index}",
                subdomain=f"program-{index}",
                tenant_key=f"tenantkey{index:011d}",
                owner=owner,
            )
            TenantDashboardMetric.objects.create(tenant=tenant, member_count=index)

        # django-tenants emits SET search_path before each of the five bounded
        # public queries (count, tenants, domains, subscriptions, metrics).
        with self.assertNumQueries(10):
            page, metrics = views._founder_tenant_page(1)
            self.assertEqual(len(page.object_list), 25)
            self.assertEqual(len(metrics), 25)

    def test_dashboard_renders_a_page_with_a_tenant(self):
        founder = get_user_model().objects.create_user(
            username="founder-dashboard",
            email="founder-dashboard@example.com",
            password="secret123",
            is_staff=True,
        )
        owner = OwnerUser.objects.create(
            email="dashboard-owner@example.com",
            program_name="Dashboard Program",
            password="hashed",
        )
        tenant = CRTenant.objects.create(
            name=owner.program_name,
            schema_name="dashboard_program",
            subdomain="dashboard-program",
            tenant_key="dashkey1234567890123",
            owner=owner,
        )
        TenantDashboardMetric.objects.create(tenant=tenant, member_count=1)

        self.client.force_login(founder)
        response = self.client.get(reverse("founder_saas_system_control"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, owner.email)


class ExternalTableRecordCountTests(TestCase):
    def test_increment_updates_counter_without_a_count_query(self):
        table = ExternalTable.objects.create(table_name="counter_test")

        with self.assertNumQueries(2):
            views._increment_external_table_record_count(table)

        table.refresh_from_db()
        self.assertEqual(table.record_count, 1)

    def test_decrement_never_makes_counter_negative(self):
        table = ExternalTable.objects.create(table_name="counter_floor", record_count=0)

        views._decrement_external_table_record_count(table)

        table.refresh_from_db()
        self.assertEqual(table.record_count, 0)


class ExternalTableRecordRegistrationTests(CacheIsolationMixin, TestCase):
    def test_signup_endpoint_uses_bounded_queries(self):
        table = ExternalTable.objects.create(table_name="signup_query_budget")
        User.objects.create(
            full_name="Signup User",
            registration_number="REG-QUERY-001",
        )
        request = RequestFactory().post(
            "/api/external-tables/signup/",
            data=json.dumps(
                {
                    "table_id": table.id,
                    "registration_number": "REG-QUERY-001",
                    "name": "Signup User",
                    "email": "signup@example.com",
                }
            ),
            content_type="application/json",
        )

        with (
            patch("core.views._rate_limit", return_value=False),
            patch("core.views._scope_external_tables_queryset", return_value=ExternalTable.objects.all()),
            patch("core.views._scope_users_queryset", return_value=User.objects.all()),
            # django-tenants emits six schema switches; transaction.atomic()
            # emits a savepoint/release pair in TestCase, alongside five
            # bounded business queries.
            self.assertNumQueries(14),
        ):
            response = views.api_external_table_signup(request)

        self.assertEqual(response.status_code, 201)

    def test_registration_number_is_unique_within_a_table(self):
        table = ExternalTable.objects.create(table_name="registration_constraint")
        ExternalTableRecord.objects.create(
            table=table,
            data={"registration_number": "REG-001"},
        )

        with self.assertRaises(IntegrityError):
            ExternalTableRecord.objects.create(
                table=table,
                data={"registration_number": "REG-001"},
            )

    def test_records_without_a_registration_number_are_not_constrained(self):
        table = ExternalTable.objects.create(table_name="unidentified_records")
        ExternalTableRecord.objects.create(table=table, data={"note": "first"})
        ExternalTableRecord.objects.create(table=table, data={"note": "second"})

        self.assertEqual(table.records.count(), 2)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)
class TenantSystemIsolationTests(CacheIsolationMixin, TestCase):
    def setUp(self):
        self.owner, owner_created = OwnerUser.objects.get_or_create(
            email="owner-api@example.com",
            defaults={
                "program_name": "BCIT",
                "password": make_password("secret123"),
                "is_owner": True,
                "is_active": True,
            },
        )
        if owner_created:
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

        self.tenant = getattr(self.owner, "tenant", None)
        if self.tenant is None:
            from customers.models import TenantSubscription

            self.tenant = CRTenant.objects.create(
                name="BCIT",
                schema_name="bcit_tenant_isolation",
                subdomain="bcit-tenant-isolation",
                tenant_key="tenantkey01234567890",
                owner=self.owner,
                is_active=True,
                provisioning_state=CRTenant.PROVISIONING_READY,
                paid_until=None,
                subscription_start=None,
                is_trial=True,
            )
            TenantSubscription.objects.create(
                tenant=self.tenant,
                plan="trial",
                status=TenantSubscription.STATUS_TRIAL,
                start_date=self.tenant.created_on,
                end_date=self.tenant.created_on + timedelta(days=14),
                is_active=True,
            )
        elif self.tenant.provisioning_state != CRTenant.PROVISIONING_READY:
            self.tenant.provisioning_state = CRTenant.PROVISIONING_READY
            self.tenant.save(update_fields=["provisioning_state"])

        session = self.client.session
        session["service_user"] = {
            "user_type": "owner",
            "owner_id": self.owner.id,
            "email": self.owner.email,
            "program_name": self.owner.program_name,
        }
        session["tenant_id"] = self.tenant.id
        session["tenant_subdomain"] = self.tenant.subdomain
        session["tenant_key"] = self.tenant.tenant_key
        session.save()

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

    def test_tenant_signup_setting_get_is_public(self):
        self.client.post(f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/logout/", follow=True)

        response = self.client.get(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/signup-setting/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload["success"])

    def test_groups_page_and_groups_api_get_are_public(self):
        self.client.post(f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/logout/", follow=True)

        page_response = self.client.get(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/groups/",
        )
        api_response = self.client.get(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/groups/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(page_response.status_code, 200)
        self.assertEqual(api_response.status_code, 200)
        payload = json.loads(api_response.content)
        self.assertTrue(payload["success"])

    def test_public_group_member_summary_has_name_without_directory_pii(self):
        group = UserGroup.objects.create(
            group_name="Public Group", max_members=10, created_by=self.owner.id
        )
        user = User.objects.create(
            full_name="Visible Group Member",
            registration_number="PUBLIC-GROUP-001",
            email="private@example.com",
            phone="123456",
            created_by=self.owner.id,
            group_name=group.group_name,
        )
        UserGroupMember.objects.create(user_id=user.id, group_id=group.id, status="active")

        self.client.post(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/logout/",
            follow=True,
        )
        path = f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/groups/?include_members=1"
        # django-tenants emits four schema switches and pagination counts the
        # group queryset. The final bulk user-name query makes this bounded at
        # ten queries, regardless of how many members the group contains.
        with self.assertNumQueries(10):
            response = self.client.get(path, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

        self.assertEqual(response.status_code, 200)
        member = response.json()["data"][0]["members"][0]
        self.assertEqual(member["display_name"], "Visible Group Member")
        self.assertEqual(
            set(member), {"id", "user_id", "is_leader", "joined_at", "display_name"}
        )

    def test_properties_and_external_tables_pages_are_public(self):
        self.client.post(f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/logout/", follow=True)

        properties_response = self.client.get(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/properties/",
        )
        tables_response = self.client.get(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/external-tables/",
        )

        self.assertEqual(properties_response.status_code, 200)
        self.assertEqual(tables_response.status_code, 200)

    def test_public_service_external_tables_page_returns_200(self):
        response = self.client.get('/service/external-tables/')
        self.assertEqual(response.status_code, 200)

    def test_public_service_external_tables_api_returns_empty_list(self):
        response = self.client.get('/service/api/external-tables/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload['success'])
        self.assertEqual(payload['data'], [])
        self.assertEqual(payload['meta']['total_count'], 0)

    def test_tenant_properties_api_persists_to_database_and_is_publicly_readable(self):
        create_response = self.client.post(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/properties/",
            data=json.dumps(
                {
                    "item_name": "Black Wallet",
                    "description": "Wallet found near the hall",
                    "category": "other",
                    "location": "Main Hall",
                    "date_found": "2026-04-16",
                    "image_url": "https://example.com/wallet.jpg",
                    "contact_info": "finder@example.com",
                }
            ),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(create_response.status_code, 201)

        self.client.post(f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/logout/", follow=True)

        list_response = self.client.get(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/properties/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(list_response.status_code, 200)
        payload = json.loads(list_response.content)
        self.assertTrue(payload["success"])
        self.assertEqual(len(payload["data"]), 1)
        self.assertEqual(payload["data"][0]["item_name"], "Black Wallet")

    def test_user_group_assignment_and_move_keep_memberships_in_sync(self):
        base_path = f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api"
        first_group = UserGroup.objects.create(
            group_name="Group A", max_members=10, created_by=self.owner.id
        )
        second_group = UserGroup.objects.create(
            group_name="Group B", max_members=10, created_by=self.owner.id
        )

        create_response = self.client.post(
            f"{base_path}/users/",
            data=json.dumps({
                "full_name": "Grouped Member",
                "registration_number": "GROUP-001",
                "group_name": first_group.group_name,
            }),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(create_response.status_code, 201)
        user_id = create_response.json()["data"]["id"]

        first_group.refresh_from_db()
        self.assertEqual(first_group.current_members, 1)
        self.assertTrue(UserGroupMember.objects.filter(
            user_id=user_id, group_id=first_group.id, status="active"
        ).exists())

        move_response = self.client.post(
            f"{base_path}/groups/move-member/",
            data=json.dumps({"user_id": user_id, "target_group_id": second_group.id}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(move_response.status_code, 200)
        self.assertFalse(UserGroupMember.objects.filter(
            user_id=user_id, group_id=first_group.id, status="active"
        ).exists())
        self.assertTrue(UserGroupMember.objects.filter(
            user_id=user_id, group_id=second_group.id, status="active"
        ).exists())
        self.assertEqual(User.objects.get(id=user_id).group_name, second_group.group_name)

        # The system-index "add member" action uses this user update endpoint.
        # It must recreate the canonical membership rather than only changing
        # the legacy group_name string.
        update_response = self.client.put(
            f"{base_path}/users/{user_id}/",
            data=json.dumps({"group_name": first_group.group_name}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(update_response.status_code, 200)
        membership = UserGroupMember.objects.get(user_id=user_id, group_id=first_group.id)
        self.assertEqual(User.objects.get(id=user_id).group_name, first_group.group_name)

        # Removing that membership must also clear the denormalized user field
        # so the groups and user-management screens cannot disagree.
        remove_response = self.client.delete(
            f"{base_path}/group-members/{membership.id}/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(remove_response.status_code, 200)
        self.assertEqual(User.objects.get(id=user_id).group_name, "")

        groups_response = self.client.get(
            f"{base_path}/groups/?include_members=1", HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        groups_by_id = {group["id"]: group for group in groups_response.json()["data"]}
        self.assertEqual(groups_by_id[first_group.id]["members"], [])
        self.assertEqual(groups_by_id[second_group.id]["members"], [])

    def test_public_property_claim_cannot_change_owner_managed_fields(self):
        prop = Property.objects.create(
            item_name="Original Wallet",
            description="Found by reception",
            category="other",
            location="Reception",
            created_by=self.owner.id,
        )
        path = f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/properties/{prop.id}/"
        self.client.post(f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/logout/", follow=True)

        response = self.client.put(
            path,
            data=json.dumps({
                "status": "claimed",
                "claimant_name": "Claimant",
                "claimant_contact": "claimant@example.com",
                "claim_proof": "Receipt",
                "item_name": "Attempted overwrite",
                "location": "Attempted overwrite",
            }),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        prop.refresh_from_db()
        self.assertEqual(prop.status, "claimed")
        self.assertEqual(prop.claimant_name, "Claimant")
        self.assertEqual(prop.item_name, "Original Wallet")
        self.assertEqual(prop.location, "Reception")
