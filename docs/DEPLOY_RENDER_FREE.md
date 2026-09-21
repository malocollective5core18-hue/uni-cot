# Render Free deployment guide

This project supports a Render free-tier deployment with one web service. The web service remains alive via a monitored keepalive, exposes lightweight health endpoints, and can run the bounded in-process tenant provisioner after its Gunicorn worker forks.

## Required services

1. Web service
   - Runs Django via Gunicorn.
   - Exposes `/healthz/` and `/readyz/`.
   - Uses the same environment variables as the standard app.
   - Must be kept alive by an external monitor such as UptimeRobot or Render's own health checks.

2. In-process provisioner
   - Enabled only with `TENANT_PROVISION_INPROCESS=true`.
   - Starts from Gunicorn's `post_fork` hook, drains due jobs once, then wakes every 120 seconds.
   - Uses a PostgreSQL advisory lock, so only one web worker performs provisioning if the service is scaled later.

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

The Free-tier production contract is:

- Owner registration enqueues a provisioning job.
- Gunicorn's in-process provisioner works the queue when `TENANT_PROVISION_INPROCESS=true`.
- Once the tenant is ready, the owner can access the tenant workspace through the routed path.
- A pending tenant remains intentionally blocked until provisioning succeeds.

This is a Free-tier fallback, not a high-throughput worker architecture. Provisioning remains serialized, and long schema migrations can still consume the one web worker's resources.

## Render-specific notes

- Free-tier services are not a substitute for a production-ready always-on worker.
- Set `DJANGO_TRUSTED_PROXY_IPS=127.0.0.1` for Render.  The app accepts
  `X-Forwarded-For` for rate-limit identity only when the direct connection is
  from this configured proxy peer; do not add public client networks here.
- Keep the web service alive with an external monitor so the app is not silently suspended by a cold start.
- Monitor Postgres expiry, database connectivity, and queue backlog after each deploy.
- Back up tenant data and schema changes outside the free-tier plan if the service is used for real customer workloads.

## Example process commands

Web service:

```bash
gunicorn mysite.wsgi:application --bind 0.0.0.0:$PORT --workers 1
```

Keep this WSGI command as the deployed command until the Phase 1 ASGI socket benchmark is recorded as acceptable. The planned ASGI command is documented in `docs/REALTIME_DESIGN.md` and must not be deployed yet.

### Optional public realtime rollout

Public WebSockets are disabled by default. To enable the separately reviewed
feature, set `REALTIME_PUBLIC_ENABLED=true` and use the ASGI command only after
the socket measurement gate passes:

```bash
gunicorn mysite.asgi:application -k uvicorn_worker.UvicornWorker --workers 1 --bind 0.0.0.0:$PORT -c gunicorn.conf.py
```

Conservative limits are configurable with `REALTIME_PUBLIC_MAX_SOCKETS_PER_IP`
(3), `REALTIME_PUBLIC_MAX_SOCKETS_PER_TENANT` (50),
`REALTIME_PUBLIC_MAX_SOCKETS_TOTAL` (100), and
`REALTIME_PUBLIC_CONNECTION_LIMIT` (20 attempts per window). The socket sends
only resource/version notifications; HTTP ETag endpoints remain authoritative
and polling remains the fallback. Set `REALTIME_PUBLIC_ENABLED=false` to turn
it off immediately. Roll back to the WSGI command above if memory, latency,
Redis health, or disconnect rates are unacceptable.

Bootstrap/manual deployment step:

```bash
python manage.py bootstrap_render
```

This is the operational contract: health checks must pass, the in-process provisioner must drain queued jobs, and the tenant registry should reach a ready state before owners are expected to sign in.
