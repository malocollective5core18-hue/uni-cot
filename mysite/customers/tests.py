from datetime import timedelta
from types import SimpleNamespace
from importlib import import_module
from unittest.mock import Mock, call, patch

from django.apps import apps
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django_tenants.models import TenantMixin
from django.utils import timezone
from django.test.utils import CaptureQueriesContext
from django.db import connection

from customers.management.commands.provision_tenants import claim_next_job, recover_stale_jobs, run_job
from customers.provisioning import schedule_tenant_provisioning
from customers.models import (
    CRTenant,
    Domain,
    TenantProvisioningJob,
    TenantSubscription,
    create_owner_tenant,
)
from mysite.tenant_middleware import TenantMiddleware
from mysite.tests.helpers import CacheIsolationMixin
from service.models import OwnerUser


class PathTenantProvisioningTests(CacheIsolationMixin, TestCase):
    @patch("customers.management.commands.provision_tenants.run_job")
    @patch("customers.management.commands.provision_tenants.claim_next_job")
    @patch("customers.management.commands.provision_tenants.recover_stale_jobs", return_value=0)
    def test_drain_processes_all_due_jobs_then_exits(
        self, mocked_recover, mocked_claim, mocked_run
    ):
        first_job = SimpleNamespace(tenant_id=1, tenant=SimpleNamespace(schema_name="first"))
        second_job = SimpleNamespace(tenant_id=2, tenant=SimpleNamespace(schema_name="second"))
        mocked_claim.side_effect = [first_job, second_job, None]

        call_command("provision_tenants", "--drain")

        self.assertEqual(mocked_claim.call_count, 3)
        self.assertEqual(mocked_run.call_args_list, [call(first_job), call(second_job)])

    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    @patch("customers.management.commands.bootstrap_render.call_command")
    @patch("customers.management.commands.bootstrap_render.Command.ensure_superuser")
    @patch("customers.management.commands.bootstrap_render.Command.ensure_public_owner_user_columns")
    def test_render_bootstrap_drains_due_tenant_jobs(
        self, mocked_owner_columns, mocked_superuser, mocked_call_command
    ):
        call_command("bootstrap_render")

        mocked_call_command.assert_called_once_with(
            "provision_tenants", "--drain", verbosity=1
        )

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

    def test_existing_tenants_are_marked_ready_by_provisioning_migration(self):
        mark_existing_tenants_ready = import_module(
            "customers.migrations.0007_tenant_provisioning_lifecycle"
        ).mark_existing_tenants_ready

        owner = OwnerUser.objects.create(
            email="migration-owner@example.com",
            program_name="Migration Check",
            password="hashed",
            is_owner=True,
            is_active=True,
        )
        tenant = CRTenant.objects.create(
            name="Preexisting",
            schema_name="preexisting",
            subdomain="preexisting",
            tenant_key="A" * 20,
            owner=owner,
            provisioning_state=CRTenant.PROVISIONING_PENDING,
        )

        with connection.schema_editor() as schema_editor:
            mark_existing_tenants_ready(apps, schema_editor)

        tenant.refresh_from_db()
        self.assertEqual(tenant.provisioning_state, CRTenant.PROVISIONING_READY)

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
        self.assertIsNone(claim_next_job())
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
        self.assertGreater((job.next_attempt_at - timezone.now()).total_seconds(), 25)
        self.assertLessEqual((job.next_attempt_at - timezone.now()).total_seconds(), 30)
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
        job.locked_at = timezone.now() - timedelta(minutes=11)
        job.save(update_fields=["status", "locked_at"])

        self.assertEqual(recover_stale_jobs(), 1)
        job.refresh_from_db()
        self.assertEqual(job.status, TenantProvisioningJob.STATUS_RETRY)
        self.assertIsNone(job.locked_at)
        self.assertIn("lock expired", job.last_error)

    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    def _tenant_for_routing_state(self, state):
        owner = OwnerUser.objects.create(
            email=f"{state}@example.com",
            program_name=f"{state.title()} Tenant",
            password="hashed",
            is_owner=True,
            is_active=True,
        )
        return CRTenant.objects.create(
            name=f"{state.title()} Tenant",
            schema_name=f"{state}_tenant",
            subdomain=f"{state}-tenant",
            tenant_key=(state[0].upper() * 20),
            owner=owner,
            is_active=True,
            provisioning_state=state,
        )

    def _path_request_for(self, tenant):
        return RequestFactory().get(
            f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/system/"
        )

    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    def test_pending_tenant_path_shows_setup_page_without_activating_schema(self):
        tenant = self._tenant_for_routing_state(CRTenant.PROVISIONING_PENDING)
        get_response = Mock()
        middleware = TenantMiddleware(get_response)
        request = self._path_request_for(tenant)
        response = middleware(request)

        self.assertEqual(response.status_code, 202)
        self.assertContains(response, "Workspace is being set up", status_code=202)
        self.assertFalse(hasattr(request, "tenant"))
        get_response.assert_not_called()

    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    def test_provisioning_tenant_path_shows_setup_page_without_activating_schema(self):
        tenant = self._tenant_for_routing_state(CRTenant.PROVISIONING_IN_PROGRESS)
        get_response = Mock()
        middleware = TenantMiddleware(get_response)

        response = middleware(self._path_request_for(tenant))

        self.assertEqual(response.status_code, 202)
        self.assertContains(response, "Workspace is being set up", status_code=202)
        get_response.assert_not_called()

    @patch("customers.management.commands.process_provisioning_jobs.run_job", side_effect=RuntimeError("job failed"))
    @patch("customers.management.commands.process_provisioning_jobs.claim_next_job")
    @patch("customers.management.commands.process_provisioning_jobs.recover_stale_jobs", return_value=0)
    def test_process_command_exits_normally_when_job_fails(self, mocked_recover, mocked_claim, mocked_run):
        job = SimpleNamespace(tenant_id=77, attempts=1, tenant=SimpleNamespace(schema_name="failed_schema"))
        mocked_claim.side_effect = [job, None]

        call_command("process_provisioning_jobs", "--once")

        self.assertEqual(mocked_run.call_count, 1)
        self.assertEqual(mocked_claim.call_count, 2)

    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    @patch("customers.provisioning.threading.Thread")
    def test_request_schedule_is_throttled_per_tenant(self, mocked_thread):
        from customers import provisioning

        provisioning._REQUEST_KICKS.clear()
        self.assertTrue(schedule_tenant_provisioning(9001))
        self.assertFalse(schedule_tenant_provisioning(9001))
        self.assertEqual(mocked_thread.call_count, 1)
        provisioning._REQUEST_KICKS.clear()

    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    @patch("customers.provisioning.schedule_tenant_provisioning")
    def test_due_job_response_has_retry_after_refresh_and_triggers_provisioner(self, mocked_schedule):
        tenant = self._tenant_for_routing_state(CRTenant.PROVISIONING_PENDING)
        job = TenantProvisioningJob.objects.create(tenant=tenant, next_attempt_at=timezone.now() - timedelta(seconds=1))
        response = TenantMiddleware(Mock())(self._path_request_for(tenant))

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response["Retry-After"], "5")
        self.assertIn(b'http-equiv="refresh" content="5"', response.content)
        mocked_schedule.assert_called_once_with(tenant.id)

    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    @patch("customers.provisioning.schedule_tenant_provisioning")
    def test_static_admin_and_health_paths_do_not_trigger_provisioner(self, mocked_schedule):
        middleware = TenantMiddleware(Mock())
        for path in ("/healthz/", "/static/site.css", "/admin/", "/internal/run-provisioning/"):
            request = RequestFactory().get(path)
            self.assertTrue(middleware._request_excluded_from_provisioning(request))
        mocked_schedule.assert_not_called()

    @patch.dict("os.environ", {"PROVISIONING_CRON_TOKEN": "test-token"}, clear=False)
    @patch("customers.provisioning.start_provisioning_batch")
    def test_provisioning_pinger_accepts_header_or_query_token(self, mocked_start):
        from customers.provisioning import run_provisioning_endpoint

        for request in (
            RequestFactory().get("/internal/run-provisioning/", HTTP_X_PROVISIONING_TOKEN="test-token"),
            RequestFactory().post("/internal/run-provisioning/?token=test-token"),
        ):
            response = run_provisioning_endpoint(request)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"accepted": True})
        self.assertEqual(mocked_start.call_count, 2)

    @patch.dict("os.environ", {"PROVISIONING_CRON_TOKEN": "test-token"}, clear=False)
    @patch("customers.provisioning.start_provisioning_batch")
    def test_provisioning_pinger_rejects_missing_or_wrong_token(self, mocked_start):
        from customers.provisioning import run_provisioning_endpoint

        requests = (
            RequestFactory().get("/internal/run-provisioning/"),
            RequestFactory().get("/internal/run-provisioning/?token=wrong"),
            RequestFactory().post("/internal/run-provisioning/", HTTP_X_PROVISIONING_TOKEN="wrong"),
        )
        for request in requests:
            response = run_provisioning_endpoint(request)
            self.assertEqual(response.status_code, 403)
            self.assertNotIn(b"tenant", response.content.lower())
        mocked_start.assert_not_called()

    @patch("customers.provisioning.threading.Thread")
    def test_pinger_batch_has_process_level_lock(self, mocked_thread):
        from customers import provisioning

        provisioning._CRON_RUN_LOCK.release() if provisioning._CRON_RUN_LOCK.locked() else None
        try:
            self.assertTrue(provisioning.start_provisioning_batch())
            self.assertFalse(provisioning.start_provisioning_batch())
            self.assertEqual(mocked_thread.call_count, 1)
        finally:
            if provisioning._CRON_RUN_LOCK.locked():
                provisioning._CRON_RUN_LOCK.release()

    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    @patch("customers.management.commands.provision_tenants.tenant_schema_is_healthy", return_value=False)
    @patch("customers.management.commands.provision_tenants.ensure_tenant_schema_ready")
    def test_tenant_stays_unready_when_schema_migrations_are_not_confirmed(self, mocked_ensure, mocked_health):
        tenant = self._tenant_for_routing_state(CRTenant.PROVISIONING_PENDING)
        job = tenant.provisioning_job if hasattr(tenant, "provisioning_job") else TenantProvisioningJob.objects.create(tenant=tenant)
        claimed = claim_next_job(tenant.id)
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            run_job(claimed)
        tenant.refresh_from_db()
        job.refresh_from_db()
        self.assertNotEqual(tenant.provisioning_state, CRTenant.PROVISIONING_READY)
        self.assertEqual(job.status, TenantProvisioningJob.STATUS_RETRY)
        mocked_ensure.assert_called_once()
        mocked_health.assert_called_once_with(tenant)

    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    @patch("customers.management.commands.provision_tenants.MAX_ATTEMPTS", 1)
    @patch("customers.management.commands.provision_tenants.ensure_tenant_schema_ready", side_effect=RuntimeError("migration exploded"))
    def test_failed_job_logs_last_error_and_shows_terminal_message(self, mocked_ensure):
        tenant = self._tenant_for_routing_state(CRTenant.PROVISIONING_PENDING)
        job = TenantProvisioningJob.objects.create(tenant=tenant)
        claimed = claim_next_job(tenant.id)
        with self.assertRaisesRegex(RuntimeError, "migration exploded"):
            run_job(claimed)
        job.refresh_from_db()
        tenant.refresh_from_db()
        self.assertEqual(job.status, TenantProvisioningJob.STATUS_FAILED)
        self.assertEqual(job.last_error, "migration exploded")
        middleware = TenantMiddleware(Mock())
        with self.assertLogs("mysite.tenant_middleware", level="ERROR") as captured:
            response = middleware(self._path_request_for(tenant))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b"meta http-equiv", response.content)
        self.assertIn("migration exploded", "\n".join(captured.output))

    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    def test_failed_tenant_path_shows_generic_error_without_activating_schema(self):
        tenant = self._tenant_for_routing_state(CRTenant.PROVISIONING_FAILED)
        get_response = Mock()
        middleware = TenantMiddleware(get_response)

        response = middleware(self._path_request_for(tenant))

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "Workspace setup could not be completed", status_code=503)
        self.assertNotContains(response, "provisioning_error", status_code=503)
        get_response.assert_not_called()

    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    def test_ready_tenant_path_activates_the_tenant_schema(self):
        tenant = self._tenant_for_routing_state(CRTenant.PROVISIONING_READY)
        get_response = Mock(return_value=SimpleNamespace(status_code=200))
        middleware = TenantMiddleware(get_response)
        request = self._path_request_for(tenant)

        response = middleware(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(request.tenant.id, tenant.id)
        get_response.assert_called_once_with(request)

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
