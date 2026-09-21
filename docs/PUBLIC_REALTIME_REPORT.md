# Public realtime WebSocket report

Date: 2026-09-21  
Branch: `feat/public-realtime-ws`

## Scope

Anonymous tenant notifications only. The socket carries no rows or personal
data; clients refetch through existing tenant-scoped ETag APIs. The feature is
behind `REALTIME_PUBLIC_ENABLED`, which defaults to `false`.

## Gate results

| Gate | Result | Evidence |
| --- | --- | --- |
| Clean baseline and branch | PASS | Baseline 107 tests; branch created from clean HEAD. |
| Flag-off compatibility | PASS | Public socket rejects when disabled; polling code remains registered. |
| Exact ready-tenant path | PASS | Public consumer uses the existing exact tuple resolver and rejects unknown/non-ready/inactive tenants. |
| Origin and tenant isolation | PASS | Empty/foreign origins rejected; groups include tenant key; forged client messages cannot subscribe. |
| Public allowlist/minimal payload | PASS | Only slider-images, countdown-cards, public-members, and groups; payload is exactly v/type/resource/version. |
| Capacity/disconnect cleanup | PASS | Per-IP, per-tenant, global reservations and release test pass. |
| Client reconnect/catch-up/hidden fallback | PASS | Shared TenantLive socket implementation; Node test passes and the client is 2,999 bytes. |
| Django focused realtime tests | PASS | 12 public socket/capacity tests pass. |
| Full suite/checks | PASS | 119 tests pass normal, reverse, and shuffle seeds 20260932/20260933; compileall, check, deploy check, and migration check pass. |
| 100/300 socket RSS/CPU/latency measurement | NOT RUN | No safe local Redis/socket load harness was available; no numbers invented. |

## Security review

The public route accepts no tenant or subscription commands. Tenant selection is
path-only and must resolve to the exact active, subscribed, ready tenant.
Messages are limited to `ping`; owner-only `users`, private properties, and
other resources are never joined or forwarded. Events contain only a resource
name and cache version. HTTP authorization and ETag responses remain unchanged.
Per-IP, per-tenant, global, and attempt limits release counters on disconnect.

## Performance and rollout decision

**NO-GO for Render enablement pending measurement.** The implementation is
designed to avoid request storms: one socket per tab, 25-second heartbeat,
75-second idle close, conservative caps, and ETag refetches. However, RSS per
socket and page latency during 100/300 idle sockets were not measured. Keep
`REALTIME_PUBLIC_ENABLED=false` and the WSGI command until those measurements
are recorded. The existing 30-second TenantLive polling remains the safe
fallback.

## Rollback

Set `REALTIME_PUBLIC_ENABLED=false`; clients immediately stop opening public
sockets and continue polling. If ASGI itself is not acceptable, restore the
documented WSGI command without changing application data or authentication.
