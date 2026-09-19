from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import RequestFactory, TestCase
from django_tenants.models import TenantMixin
from django.utils import timezone
from django.test.utils import CaptureQueriesContext
from django.db import connection

from customers.management.commands.provision_tenants import claim_next_job, recover_stale_jobs, run_job
from customers.models import (
    CRTenant,
    Domain,
    TenantProvisioningJob,
    TenantSubscription,
    create_owner_tenant,
)
from mysite.tenant_middleware import TenantMiddleware
from service.models import OwnerUser


class PathTenantProvisioningTests(TestCase):
    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    @patch("customers.models._generate_unique_tenant_key", side_effect=["A" * 20, "B" * 20])
    def test_create_owner_tenant_retries_a_duplicate_tenant_key(self, mocked_key):
        existing_owner = OwnerUser.objects.create(
            email="existing@example.com",
            program_name="Existing",
            password="hashed",
            is_owner=True,
            is_active=True,
        )
        CRTenant.objects.create(
            name="Existing",
            schema_name="existing",
            subdomain="existing",
            tenant_key="A" * 20,
            owner=existing_owner,
        )
        owner = OwnerUser.objects.create(
            email="retry-key@example.com",
            program_name="Retry Key",
            password="hashed",
            is_owner=True,
            is_active=True,
        )

        tenant = create_owner_tenant(owner)

        self.assertEqual(tenant.tenant_key, "B" * 20)
        self.assertEqual(mocked_key.call_count, 2)

    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    def test_create_owner_tenant_skips_domain_creation_in_path_mode(self):
        owner = OwnerUser.objects.create(
            email="owner@example.com",
            program_name="Computer Science",
            password="hashed",
            is_owner=True,
            is_active=True,
        )

        tenant = create_owner_tenant(owner)

        self.assertEqual(tenant.owner, owner)
        self.assertTrue(CRTenant.objects.filter(id=tenant.id).exists())
        self.assertEqual(len(tenant.tenant_key), 20)
        self.assertFalse(Domain.objects.filter(tenant=tenant).exists())
        self.assertTrue(TenantSubscription.objects.filter(tenant=tenant, plan="trial").exists())
        self.assertEqual(tenant.provisioning_state, CRTenant.PROVISIONING_PENDING)
        self.assertEqual(tenant.provisioning_job.status, TenantProvisioningJob.STATUS_QUEUED)

    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    @patch("customers.models.ensure_tenant_schema_ready")
    def test_create_owner_tenant_queues_schema_provisioning_in_path_mode(self, mocked_ensure_schema):
        owner = OwnerUser.objects.create(
            email="owner3@example.com",
            program_name="Business",
            password="hashed",
            is_owner=True,
            is_active=True,
        )

        tenant = create_owner_tenant(owner)

        mocked_ensure_schema.assert_not_called()
        self.assertTrue(TenantProvisioningJob.objects.filter(tenant=tenant).exists())

    @patch("customers.management.commands.provision_tenants.tenant_schema_is_healthy", return_value=True)
    @patch("customers.management.commands.provision_tenants.ensure_tenant_schema_ready")
    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    def test_worker_marks_tenant_ready_after_schema_health_check(self, mocked_ensure_schema, mocked_health_check):
        owner = OwnerUser.objects.create(
            email="worker@example.com",
            program_name="Worker Test",
            password="hashed",
            is_owner=True,
            is_active=True,
        )
        tenant = create_owner_tenant(owner)

        job = claim_next_job()
        self.assertIsNotNone(job)
        run_job(job)

        tenant.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(tenant.provisioning_state, CRTenant.PROVISIONING_READY)
        self.assertEqual(job.status, TenantProvisioningJob.STATUS_SUCCEEDED)
        mocked_ensure_schema.assert_called_once_with(tenant, verbosity=0)
        mocked_health_check.assert_called_once_with(tenant)

    @patch("customers.management.commands.provision_tenants.ensure_tenant_schema_ready", side_effect=RuntimeError("schema unavailable"))
    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    def test_worker_requeues_a_failed_provisioning_attempt(self, mocked_ensure_schema):
        owner = OwnerUser.objects.create(
            email="retry@example.com",
            program_name="Retry Test",
            password="hashed",
            is_owner=True,
            is_active=True,
        )
        tenant = create_owner_tenant(owner)
        job = claim_next_job()

        with self.assertRaisesRegex(RuntimeError, "schema unavailable"):
            run_job(job)

        tenant.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(tenant.provisioning_state, CRTenant.PROVISIONING_PENDING)
        self.assertEqual(job.status, TenantProvisioningJob.STATUS_RETRY)
        self.assertEqual(job.attempts, 1)
        self.assertIn("schema unavailable", job.last_error)
        mocked_ensure_schema.assert_called_once_with(tenant, verbosity=0)

    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    def test_stale_worker_lock_is_recovered_for_retry(self):
        owner = OwnerUser.objects.create(
            email="stale@example.com",
            program_name="Stale Job",
            password="hashed",
            is_owner=True,
            is_active=True,
        )
        tenant = create_owner_tenant(owner)
        job = tenant.provisioning_job
        job.status = TenantProvisioningJob.STATUS_RUNNING
        job.locked_at = timezone.now() - timedelta(minutes=16)
        job.save(update_fields=["status", "locked_at"])

        self.assertEqual(recover_stale_jobs(), 1)
        job.refresh_from_db()
        self.assertEqual(job.status, TenantProvisioningJob.STATUS_RETRY)
        self.assertIn("lock expired", job.last_error)

    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    def test_pending_tenant_path_returns_not_found_without_running_a_view(self):
        owner = OwnerUser.objects.create(
            email="pending@example.com",
            program_name="Pending Tenant",
            password="hashed",
            is_owner=True,
            is_active=True,
        )
        tenant = create_owner_tenant(owner)
        get_response = Mock()
        middleware = TenantMiddleware(get_response)
        request = RequestFactory().get(
            f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/system/"
        )
        response = middleware(request)

        self.assertEqual(response.status_code, 404)
        get_response.assert_not_called()

    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    def test_path_tenant_resolution_uses_cached_validated_tenant(self):
        owner = OwnerUser.objects.create(
            email="cached@example.com",
            program_name="Cached Tenant",
            password="hashed",
            is_owner=True,
            is_active=True,
        )
        tenant = create_owner_tenant(owner)
        tenant.provisioning_state = CRTenant.PROVISIONING_READY
        tenant.save(update_fields=["provisioning_state"])
        middleware = TenantMiddleware(Mock())
        path_tenant = {
            "tenant_slug": tenant.subdomain,
            "tenant_id": tenant.id,
            "tenant_key": tenant.tenant_key,
        }

        self.assertEqual(middleware._resolve_path_tenant(path_tenant).id, tenant.id)
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(middleware._resolve_path_tenant(path_tenant).id, tenant.id)
        self.assertEqual(len(queries), 0)

    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    @patch.object(TenantMixin, "save", side_effect=RuntimeError("tenant mixin should not run"))
    def test_create_owner_tenant_bypasses_tenantmixin_save_in_path_mode(self, mocked_tenant_save):
        owner = OwnerUser.objects.create(
            email="owner2@example.com",
            program_name="Education",
            password="hashed",
            is_owner=True,
            is_active=True,
        )

        tenant = create_owner_tenant(owner)

        self.assertTrue(CRTenant.objects.filter(id=tenant.id).exists())
        self.assertEqual(len(tenant.tenant_key), 20)
        mocked_tenant_save.assert_not_called()
