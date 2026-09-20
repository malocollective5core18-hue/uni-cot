# Realtime update design

Date: 2026-09-20  
Phase: 0 — design only

## Goal and non-goals

Open pages in one ready tenant should learn that a resource changed without receiving the changed row over a socket. The page then uses its existing tenant-scoped API and ETag support to obtain the latest allowed representation.

This is not a presence system, a chat transport, or a replacement for HTTP authorization. Sockets do not carry member names, registration numbers, row IDs, email addresses, or record content.

## Chosen server and Render command

The implementation will use Django Channels with Redis (`channels_redis`) and the project's existing single Gunicorn worker. The intended Render web command after Phase 1 is:

```bash
gunicorn mysite.asgi:application -k uvicorn_worker.UvicornWorker --workers 1 --bind 0.0.0.0:$PORT -c gunicorn.conf.py
```

The worker count deliberately remains one for the Render Free memory budget. Normal synchronous Django views continue to run under the ASGI adapter; no view is converted to async in this work.

The requested provisioning hook cannot yet be preserved verbatim: this checkout has no `gunicorn.conf.py`. Phase 1 must add that configuration and its `post_fork` hook, using the existing in-process provisioning start function, before the Render start command is changed. Until then the documented WSGI command remains the deployed configuration.

## Channel layer configuration

Production will require `REDIS_URL` and configure `channels_redis.core.RedisChannelLayer` from it. Missing `REDIS_URL` when `DJANGO_DEBUG=false` must raise the same way the existing shared cache configuration already does. Tests and local debug use `channels.layers.InMemoryChannelLayer`; no test may contact Redis.

Proposed settings (implemented in Phase 1):

- `REALTIME_MAX_SOCKETS_PER_USER=5`
- `REALTIME_MAX_SOCKETS_TOTAL=100` (safe initial Free-tier ceiling; configurable)
- `REALTIME_HEARTBEAT_SECONDS=25`
- `REALTIME_IDLE_SECONDS=75`
- `REALTIME_CONNECTION_WINDOW_SECONDS=60`
- `REALTIME_CONNECTION_LIMIT=20` per client address/window

Socket cap and heartbeat counters have short expirations and are decremented on disconnect. They are safeguards, not identity or authorization mechanisms.

## WebSocket route and tenant resolution

The sole route will be below the existing path tenant address:

```text
/t/<tenant_slug>/<tenant_id>/<tenant_key>/ws/realtime/
```

The consumer will parse that route itself and resolve the exact tuple through a shared resolver extracted from the existing `TenantMiddleware` logic:

1. Match the path only; client messages and query parameters are ignored for tenant selection.
2. Look up exact `id`, `subdomain`, `tenant_key`, and `is_active=True`.
3. Require `provisioning_state == ready` and an active subscription.
4. Set the tenant schema only for the bounded authorization lookup, then restore the public schema in `finally`.

This matches the HTTP path-routing contract. Unknown, pending, provisioning, failed, disabled, expired, and forged tenant paths are rejected before joining a channel group. Host headers do not choose a tenant for this route.

## Session authentication and authorization

`AllowedHostsOriginValidator(AuthMiddlewareStack(...))` wraps the WebSocket application. The project also uses a custom `service_user` object in the Django session rather than Django's built-in `request.user`; therefore the consumer must validate both the session and tenant relationship after the standard stack runs:

- Owner: session `service_user.user_type` is `owner`, its `owner_id` is the resolved tenant's owner, and the owner remains active.
- Member: session `service_user.user_type` is `member`, its tenant fields match the resolved tenant, and the member remains active in that tenant schema.
- Anonymous, another tenant's owner/member, and stale sessions are rejected.

The consumer rechecks access on every heartbeat. A failed recheck closes the connection, covering logout, expired sessions, disabled users, and changed tenant readiness without trusting a client notification.

## Event and group contract

There is no client-driven subscribe message. The server assigns an authorized connection to its allowed resource groups. Each group name includes both the exact tenant key and resource:

```text
t:<tenant_key>:members
t:<tenant_key>:groups
t:<tenant_key>:posts
t:<tenant_key>:properties
t:<tenant_key>:table-records
```

Channels itself rejects colon characters in physical group names. The deployed
representation is therefore `t.<tenant_key>.<resource>`; it retains the exact
tenant key and resource components while satisfying the channel-layer name
validator. The colon form above is the logical contract notation.

Only this minimal event is sent:

```json
{"v": 1, "type": "changed", "resource": "groups", "version": 123}
```

`version` comes from the existing `ring0:v1:<schema>:<tenant>:<owner>:<family>:version` cache key used by ETags. Phase 1 will expose a safe helper that invalidates a family and returns its new version, so cache invalidation and publication cannot drift. Resource names map to existing API families; they never identify an individual object.

## Publishing behavior

HTTP write paths publish only with `transaction.on_commit`. A process-local 300 ms debounce key groups repeated writes by `(tenant_key, resource)`; Redis is used in production for cross-request coordination. The eventual callback loads the current version and sends the minimal event to its one tenant/resource group. Channel-layer errors are caught and logged: a successfully committed database write must remain successful if Redis is unavailable.

At-most-once delivery is intentional. Clients must always be able to recover from lost events through their authorized HTTP API.

## Client behavior

`static/js/realtime.js` will create one socket per tab. Pages register the resources they render; the library does not inspect or serialize page data.

- Connect/reconnect uses exponential backoff with jitter.
- Heartbeats occur every roughly 25 seconds.
- On connection and when a hidden tab becomes visible, clients run one version catch-up request for each registered resource.
- A visible tab refetches only the changed resource using its existing ETag. Hidden tabs mark it stale and fetch once when visible.
- While disconnected, a resource may use a 60-second-or-slower fallback poll; polling is paused while hidden and disabled while the socket is healthy.
- Page adapters preserve scroll position and unsaved form values, avoid replacing an item being edited, show a non-blocking changed indicator, and honor `prefers-reduced-motion` for highlights.

## Rollout boundary

Phase 0 changes documentation only. Phase 1 adds dependencies, ASGI routing, consumer, tenant/session authorization, channel layer settings, connection limits, and a tenant-free channel-layer health probe. It will also update `docs/DEPLOY_RENDER_FREE.md` with the command and required environment values.

Phases 2–4 are deliberately not started until Phase 1 is independently tested and committed.

## Required tests and measurements

The implementation phases must add tests for forged tenant paths/headers/messages, cross-tenant isolation, readiness states, anonymous and stale-session denial, origin validation, caps, minimal payloads, on-commit publication, coalescing, publish-error safety, reconnect catch-up, and the existing database-free health endpoint behavior.

Idle-socket memory and 200-socket process memory, along with normal-page latency before/after ASGI, are **NOT RUN** in Phase 0. No ASGI server or socket test harness is installed yet, so reporting numbers now would be misleading.
