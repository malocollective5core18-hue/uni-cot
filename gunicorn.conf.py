"""Gunicorn hooks for the single Render web process.

The provisioner starts only after a worker has forked.  It is intentionally
not started from AppConfig.ready(), management commands, or tests.
"""

import os


def post_fork(server, worker):
    if os.getenv("TENANT_PROVISION_INPROCESS", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return

    from customers.provisioning import start_provisioner

    start_provisioner()
