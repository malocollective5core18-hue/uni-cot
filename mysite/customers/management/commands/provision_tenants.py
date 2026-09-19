import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from customers.models import CRTenant, TenantProvisioningJob, ensure_tenant_schema_ready, tenant_schema_is_healthy


MAX_ATTEMPTS = 5
STALE_LOCK_AFTER = timedelta(minutes=15)


def recover_stale_jobs():
    """Make jobs abandoned by a crashed worker eligible for another attempt."""
    cutoff = timezone.now() - STALE_LOCK_AFTER
    return TenantProvisioningJob.objects.filter(
        status=TenantProvisioningJob.STATUS_RUNNING,
        locked_at__lt=cutoff,
    ).update(
        status=TenantProvisioningJob.STATUS_RETRY,
        next_attempt_at=timezone.now(),
        last_error="Worker lock expired; retrying provisioning.",
    )


def claim_next_job():
    """Atomically claim one due job so concurrent workers cannot provision it twice."""
    with transaction.atomic():
        job = (
            TenantProvisioningJob.objects.select_for_update(skip_locked=True)
            .select_related("tenant")
            .filter(
                status__in=[TenantProvisioningJob.STATUS_QUEUED, TenantProvisioningJob.STATUS_RETRY],
                next_attempt_at__lte=timezone.now(),
            )
            .order_by("next_attempt_at", "id")
            .first()
        )
        if not job:
            return None

        now = timezone.now()
        job.status = TenantProvisioningJob.STATUS_RUNNING
        job.attempts += 1
        job.locked_at = now
        job.save(update_fields=["status", "attempts", "locked_at", "updated_at"])
        CRTenant.objects.filter(pk=job.tenant_id).update(
            provisioning_state=CRTenant.PROVISIONING_IN_PROGRESS,
            provisioning_started_at=now,
            provisioning_error="",
        )
        return job


def run_job(job):
    """Provision one tenant. Re-running after a worker crash is safe."""
    try:
        ensure_tenant_schema_ready(job.tenant, verbosity=0)
        if not tenant_schema_is_healthy(job.tenant):
            raise RuntimeError("Tenant schema health check failed after provisioning.")
    except Exception as error:
        with transaction.atomic():
            locked_job = TenantProvisioningJob.objects.select_for_update().get(pk=job.pk)
            terminal = locked_job.attempts >= MAX_ATTEMPTS
            delay_seconds = min(300, 2 ** locked_job.attempts)
            locked_job.status = (
                TenantProvisioningJob.STATUS_FAILED if terminal else TenantProvisioningJob.STATUS_RETRY
            )
            locked_job.next_attempt_at = timezone.now() + timedelta(seconds=delay_seconds)
            locked_job.last_error = str(error)
            locked_job.save(update_fields=["status", "next_attempt_at", "last_error", "updated_at"])
            CRTenant.objects.filter(pk=locked_job.tenant_id).update(
                provisioning_state=(
                    CRTenant.PROVISIONING_FAILED if terminal else CRTenant.PROVISIONING_PENDING
                ),
                provisioning_error=str(error),
            )
        raise

    with transaction.atomic():
        locked_job = TenantProvisioningJob.objects.select_for_update().get(pk=job.pk)
        completed_at = timezone.now()
        locked_job.status = TenantProvisioningJob.STATUS_SUCCEEDED
        locked_job.last_error = ""
        locked_job.save(update_fields=["status", "last_error", "updated_at"])
        CRTenant.objects.filter(pk=locked_job.tenant_id).update(
            provisioning_state=CRTenant.PROVISIONING_READY,
            provisioning_completed_at=completed_at,
            provisioning_error="",
        )


class Command(BaseCommand):
    help = "Provision queued tenant schemas. Run as a dedicated background worker in production."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Process at most one queued job.")
        parser.add_argument("--interval", type=float, default=5.0, help="Seconds to wait between polling cycles.")

    def handle(self, *args, **options):
        while True:
            recovered = recover_stale_jobs()
            if recovered:
                self.stdout.write(self.style.WARNING(f"Recovered {recovered} stale provisioning job(s)."))
            job = claim_next_job()
            if job:
                self.stdout.write(f"Provisioning tenant id={job.tenant_id} schema={job.tenant.schema_name}")
                try:
                    run_job(job)
                except Exception as error:
                    self.stderr.write(self.style.ERROR(f"Provisioning failed for tenant id={job.tenant_id}: {error}"))
                else:
                    self.stdout.write(self.style.SUCCESS(f"Tenant id={job.tenant_id} is ready."))
            if options["once"]:
                return
            time.sleep(max(options["interval"], 0.1))
