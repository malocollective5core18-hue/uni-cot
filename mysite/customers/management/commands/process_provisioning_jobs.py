import logging

from django.core.management.base import BaseCommand

from customers.management.commands.provision_tenants import (
    _safe_error_text,
    claim_next_job,
    recover_stale_jobs,
    retry_failed_jobs,
    run_job,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Process due tenant schema provisioning jobs and exit."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", default=True, help="Process due work and exit (default).")
        parser.add_argument("--tenant-id", type=int, help="Restrict work to one tenant.")
        parser.add_argument("--limit", type=int, default=50, help="Maximum jobs to claim in this run.")
        parser.add_argument(
            "--retry-failed",
            action="store_true",
            help="Reset up to --limit terminal failed jobs before processing due work.",
        )

    def handle(self, *args, **options):
        tenant_id = options["tenant_id"]
        limit = max(1, options["limit"])
        try:
            retried = retry_failed_jobs(tenant_id=tenant_id, limit=limit) if options["retry_failed"] else 0
            if retried:
                logger.warning("Reset terminal provisioning jobs for retry count=%s tenant_id=%s", retried, tenant_id)
            recovered = recover_stale_jobs(tenant_id=tenant_id)
            if recovered:
                logger.warning("Recovered stale provisioning jobs count=%s tenant_id=%s", recovered, tenant_id)

            processed = 0
            while processed < limit:
                job = claim_next_job(tenant_id=tenant_id)
                if job is None:
                    break
                processed += 1
                try:
                    run_job(job)
                except Exception:
                    # run_job persists the safe exception text and retry/failed state.
                    logger.error(
                        "Provisioning job attempt ended with an error tenant_id=%s schema=%s attempt=%s",
                        job.tenant_id,
                        job.tenant.schema_name,
                        job.attempts,
                    )
            self.stdout.write(
                f"Processed {processed} provisioning job(s); recovered {recovered} stale lock(s); "
                f"reset {retried} failed job(s)."
            )
        except Exception as error:
            logger.error("Provisioning queue pass failed; web startup will continue: %s", _safe_error_text(error))
            self.stderr.write("Provisioning queue pass failed; see logs. Web startup will continue.")
