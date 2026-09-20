import hmac
import logging
import os
import sys
import threading
from urllib.parse import parse_qs

from django.http import JsonResponse
from django.db import connection, close_old_connections


_WAKE_EVENT = threading.Event()
_RUNNER_STARTED = False
_RUNNER_LOCK = threading.Lock()
logger = logging.getLogger(__name__)
_ADVISORY_LOCK_ID = 912847301


def kick_provisioner():
    """Wake the in-process worker after a committed tenant enqueue."""
    _WAKE_EVENT.set()


def _process_due_jobs(output=None):
    from customers.management.commands.provision_tenants import (
        claim_next_job,
        recover_stale_jobs,
        run_job,
    )

    recover_stale_jobs()
    processed = 0
    while True:
        job = claim_next_job()
        if job is None:
            return processed
        processed += 1
        if output:
            output(f"Provisioning tenant id={job.tenant_id} schema={job.tenant.schema_name}")
        try:
            run_job(job)
        except Exception as error:
            if output:
                output(f"Provisioning failed for tenant id={job.tenant_id}: {error}")


def _runner_loop():
    """Drain once, then keep one process-wide PostgreSQL-locked runner alive."""
    close_old_connections()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [_ADVISORY_LOCK_ID])
            acquired = cursor.fetchone()[0]
        if not acquired:
            logger.info("Tenant provisioner not started: another web worker owns the advisory lock.")
            return

        # The first pass drains work queued while the web process was down.
        _process_due_jobs()
        while True:
            _WAKE_EVENT.wait(timeout=120)
            _WAKE_EVENT.clear()
            _process_due_jobs()
    except Exception:
        logger.exception("In-process tenant provisioner stopped unexpectedly.")


def start_provisioner():
    """Start one daemon worker for the web process, if enabled."""
    global _RUNNER_STARTED
    if not should_start_provisioner():
        return False
    with _RUNNER_LOCK:
        if _RUNNER_STARTED:
            return
        _RUNNER_STARTED = True
    thread = threading.Thread(target=_runner_loop, name="tenant-provisioner", daemon=True)
    thread.start()
    return True


def should_start_provisioner():
    return os.getenv("TENANT_PROVISION_INPROCESS", "false").strip().lower() in {"1", "true", "yes", "on"}


def provision_tick(request):
    if os.getenv("PROVISION_TICK_ALLOW_QUERY_TOKEN", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return JsonResponse({"error": "Not found"}, status=404)

    expected = os.getenv("PROVISION_TICK_TOKEN", "")
    supplied = parse_qs(request.META.get("QUERY_STRING", "")).get("token", [""])[0]
    if not expected or not hmac.compare_digest(supplied, expected):
        return JsonResponse({"error": "Unauthorized"}, status=401)

    start_provisioner()
    kick_provisioner()
    return JsonResponse({"accepted": True}, status=202)
