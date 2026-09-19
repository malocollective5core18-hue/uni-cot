import hmac
import os
import sys
import threading
from urllib.parse import parse_qs

from django.http import JsonResponse


_WAKE_EVENT = threading.Event()
_RUNNER_STARTED = False
_RUNNER_LOCK = threading.Lock()


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
    _process_due_jobs()
    while True:
        _WAKE_EVENT.wait(timeout=120)
        _WAKE_EVENT.clear()
        _process_due_jobs()


def start_provisioner():
    """Start one daemon worker for the web process, if enabled."""
    global _RUNNER_STARTED
    with _RUNNER_LOCK:
        if _RUNNER_STARTED:
            return
        _RUNNER_STARTED = True
    thread = threading.Thread(target=_runner_loop, name="tenant-provisioner", daemon=True)
    thread.start()


def should_start_provisioner():
    if os.getenv("IN_PROCESS_TENANT_PROVISIONER", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    return command not in {
        "check",
        " makemigrations",
        "makemigrations",
        "migrate",
        "shell",
        "test",
    }


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
