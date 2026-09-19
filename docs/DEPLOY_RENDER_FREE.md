# Render Free deployment guide

This project supports a Render free-tier deployment with a web service and a separate worker service. The web service remains alive via a monitored keepalive and exposes lightweight health endpoints, while tenant provisioning continues in the worker process instead of depending on a single Gunicorn web process.

## Required services

1. Web service
   - Runs Django via Gunicorn.
   - Exposes `/healthz/` and `/readyz/`.
   - Uses the same environment variables as the standard app.
   - Must be kept alive by an external monitor such as UptimeRobot or Render's own health checks.

2. Worker service
   - Runs `python manage.py provision_tenants --interval 5`.
   - Processes queued tenant provisioning jobs in the database.
   - May also run `python manage.py bootstrap_render` as a one-shot deployment bootstrap step if you need to reconcile the public tenant and queued jobs during a manual deploy.

## Health endpoints

- `/healthz/` returns plain text `ok` and performs no database or session work.
- `/readyz/` runs a minimal database and cache probe and returns:
  - `200` when both checks succeed.
  - `503` when the database or cache is unavailable.

These endpoints are intentionally lightweight so Render or an external monitor can validate service health without hitting application logic or tenant-specific paths.

## Provisioning tick safety net

The app also supports an optional `/internal/provision-tick/?token=...` endpoint for a manual wake-up trigger when a worker is not yet running or needs a kick after a write. This is not a replacement for the dedicated worker; it is only a backup signal used in an emergency or during a deploy.

Enable it with:

- `PROVISION_TICK_ALLOW_QUERY_TOKEN=true`
- `PROVISION_TICK_TOKEN=<shared-secret>`

The token must be supplied as a URL query parameter. Keep the token private and do not expose it in URLs meant for public logs or screenshots.

## Worker behavior

The in-process fell-back worker is enabled by default when the app is running in a normal web process. It wakes up after a tenant enqueue and polls every 120 seconds when idle.

The intended production contract is:

- Owner registration enqueues a provisioning job.
- `python manage.py provision_tenants` works the queue.
- Once the tenant is ready, the owner can access the tenant workspace through the routed path.
- A pending tenant remains intentionally blocked until provisioning succeeds.

Do not rely on the web service alone to perform provisioning work in free-tier Render. A single Gunicorn process is not a safe place for long-lived tenant schema work.

## Render-specific notes

- Free-tier services are not a substitute for a production-ready always-on worker.
- Keep the web service alive with an external monitor so the app is not silently suspended by a cold start.
- Monitor Postgres expiry, database connectivity, and queue backlog after each deploy.
- Back up tenant data and schema changes outside the free-tier plan if the service is used for real customer workloads.

## Example process commands

Web service:

```bash
gunicorn mysite.wsgi:application --bind 0.0.0.0:$PORT --workers 1
```

Worker service:

```bash
python manage.py provision_tenants --interval 5
```

Bootstrap/manual deployment step:

```bash
python manage.py bootstrap_render
```

This is the operational contract: health checks must pass, the dedicated worker must drain queued jobs, and the tenant registry should reach a ready state before owners are expected to sign in.
