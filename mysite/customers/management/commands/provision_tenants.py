import logging
import re
import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from customers.models import CRTenant, TenantProvisioningJob, ensure_tenant_schema_ready, tenant_schema_is_healthy


logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
STALE_LOCK_AFTER = timedelta(minutes=10)
BACKOFF_BASE_SECONDS = 30
BACKOFF_MAX_SECONDS = 1800


def _safe_error_text(error):
    """Keep credentials out of persisted exception messages and logs."""
    text = str(error)
    return re.sub(r"(?i)(postgres(?:ql)?://)[^/@\s]+@", r"\1[redacted]@", text)


def recover_stale_jobs(tenant_id=None):
    """Make jobs abandoned by a crashed worker eligible for another attempt."""
    now = timezone.now()
    jobs = TenantProvisioningJob.objects.filter(
        status=TenantProvisioningJob.STATUS_RUNNING,
        locked_at__lt=now - STALE_LOCK_AFTER,
    )
    if tenant_id is not None:
        jobs = jobs.filter(tenant_id=tenant_id)
    return jobs.update(
        status=TenantProvisioningJob.STATUS_RETRY,
        next_attempt_at=now,
        locked_at=None,
        last_error="Worker lock expired; retrying provisioning.",
    )


def claim_next_job(tenant_id=None):
    """Atomically claim one due job so concurrent workers cannot provision it twice."""
    with transaction.atomic():
        candidates = TenantProvisioningJob.objects.select_for_update(skip_locked=True).select_related("tenant").filter(
            status__in=[TenantProvisioningJob.STATUS_QUEUED, TenantProvisioningJob.STATUS_RETRY],
            next_attempt_at__lte=timezone.now(),
        )
        if tenant_id is not None:
            candidates = candidates.filter(tenant_id=tenant_id)
        job = candidates.order_by("next_attempt_at", "id").first()
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
    started = time.monotonic()
    tenant_id = job.tenant_id
    schema_name = job.tenant.schema_name
    attempt = job.attempts
    logger.info("Tenant provisioning started tenant_id=%s schema=%s attempt=%s", tenant_id, schema_name, attempt)
    try:
        ensure_tenant_schema_ready(job.tenant, verbosity=0)
        if not tenant_schema_is_healthy(job.tenant):
            raise RuntimeError("Tenant schema or migrations are incomplete after provisioning.")
    except Exception as error:
        duration = time.monotonic() - started
        error_text = _safe_error_text(error)
        with transaction.atomic():
            locked_job = TenantProvisioningJob.objects.select_for_update().get(pk=job.pk)
            terminal = locked_job.attempts >= MAX_ATTEMPTS
            delay_seconds = min(BACKOFF_MAX_SECONDS, BACKOFF_BASE_SECONDS * (2 ** max(locked_job.attempts - 1, 0)))
            locked_job.status = TenantProvisioningJob.STATUS_FAILED if terminal else TenantProvisioningJob.STATUS_RETRY
            locked_job.next_attempt_at = timezone.now() + timedelta(seconds=delay_seconds)
            locked_job.locked_at = None
            locked_job.last_error = error_text
            locked_job.save(update_fields=["status", "next_attempt_at", "locked_at", "last_error", "updated_at"])
            CRTenant.objects.filter(pk=locked_job.tenant_id).update(
                provisioning_state=CRTenant.PROVISIONING_FAILED if terminal else CRTenant.PROVISIONING_PENDING,
                provisioning_error=error_text,
            )
        logger.error(
            "Tenant provisioning failed tenant_id=%s schema=%s attempt=%s duration=%.3fs error=%s",
            tenant_id, schema_name, attempt, duration, error_text,
        )
        raise

    with transaction.atomic():
        locked_job = TenantProvisioningJob.objects.select_for_update().get(pk=job.pk)
        completed_at = timezone.now()
        locked_job.status = TenantProvisioningJob.STATUS_SUCCEEDED
        locked_job.locked_at = None
        locked_job.last_error = ""
        locked_job.save(update_fields=["status", "locked_at", "last_error", "updated_at"])
        CRTenant.objects.filter(pk=locked_job.tenant_id).update(
            provisioning_state=CRTenant.PROVISIONING_READY,
            provisioning_completed_at=completed_at,
            provisioning_error="",
        )
    logger.info(
        "Tenant provisioning succeeded tenant_id=%s schema=%s attempt=%s duration=%.3fs",
        tenant_id, schema_name, attempt, time.monotonic() - started,
    )


class Command(BaseCommand):
    help = "Provision queued tenant schemas. Run as a dedicated background worker in production."

    def add_arguments(self, parser):
        run_mode = parser.add_mutually_exclusive_group()
        run_mode.add_argument("--once", action="store_true", help="Process at most one queued job.")
        run_mode.add_argument(
            "--drain",
            action="store_true",
            help="Process all currently due jobs, then exit.",
        )
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
                    self.stderr.write(self.style.ERROR(f"Provisioning failed for tenant id={job.tenant_id}: {_safe_error_text(error)}"))
                else:
                    self.stdout.write(self.style.SUCCESS(f"Tenant id={job.tenant_id} is ready."))
            if options["once"]:
                return
            if options["drain"]:
                if job is None:
                    return
                continue
            time.sleep(max(options["interval"], 0.1))
