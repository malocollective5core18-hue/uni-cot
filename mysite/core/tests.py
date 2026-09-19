import json

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from core import views
from core.models import ExternalTable, User
from customers.models import CRTenant, TenantDashboardMetric
from service.models import OwnerUser


class SessionConfigurationTests(SimpleTestCase):
    def test_sessions_are_cache_backed_and_not_saved_on_every_request(self):
        self.assertEqual(settings.SESSION_ENGINE, 'django.contrib.sessions.backends.cache')
        self.assertEqual(settings.SESSION_CACHE_ALIAS, 'default')
        self.assertFalse(settings.SESSION_SAVE_EVERY_REQUEST)
        self.assertEqual(settings.SESSION_COOKIE_AGE, 60 * 60 * 24 * 7)


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


class FounderDashboardQueryTests(TestCase):
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
