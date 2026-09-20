# Tenant live polling report

Date: 2026-09-21  
Branch: `feat/tenant-live-polling`  
Scope: Phase A only — the four tenant templates. No authentication, tenant
routing, WebSocket, model, migration, dependency, or production setting was
changed.

## What changed

`static/js/tenant-live.js` is the shared browser client. Each resource has one
recursive timeout, a single in-flight request, a 30-second minimum interval,
initial jitter, ETag/304 handling, hidden-tab pause, visibility catch-up,
exponential backoff, `Retry-After`, and terminal handling for 401/403.
`TenantSync` mutations call the same resource refresh path. While a form or
dialog is active, a response is marked as updated instead of replacing the
page data.

Adapters were added independently to:

- `templates/system_index.html`: slider images, countdown cards, groups, and
  members; private users use the owner flag and public sessions use
  `/api/public-members/`.
- `templates/groups.html`: members and groups.
- `templates/external_tables.html`: external table records.
- `templates/properties.html`: properties.

Decorative clocks, animations, and the image auto-advance interval were left
alone. No anonymous WebSocket was added; Phase B remains separate.

## Request-rate estimate

At the normal 30-second floor, each registered resource makes at most about
2 requests/minute while visible (plus a 0–5 second initial jitter). A page
with four active resources is therefore about 8 requests/minute in the steady
state. A 304 still performs the HTTP request but does not re-render. Hidden
pages make no polling requests; a visible transition performs one immediate
ETag check. Errors back off up to 5 minutes, and 429 responses honor
`Retry-After`.

## Verification

| Check | Result | Evidence |
| --- | --- | --- |
| Baseline before branch | PASS | 85 tests, OK |
| `compileall` / JS syntax | PASS | `.venv/bin/python -m compileall -q .`, `node --check` |
| Shared client Node tests | PASS | 2 timer/visibility tests |
| `manage.py check` | PASS | No issues |
| `manage.py check --deploy` | PASS with warning | Placeholder secret warning only |
| `makemigrations --check --dry-run` | PASS | No changes detected |
| Full normal suite | PASS | 87 tests, OK (two polling regression tests added) |
| Full reverse suite | PASS | 87 tests, OK |
| Shuffle seed `20260930` | PASS | 87 tests, OK |
| Shuffle seed `20260931` | PASS | 87 tests, OK |
| Browser cross-device test | NOT RUN | No browser harness available |

All Django commands used only `postgres:///uni_cot_dev`; no production
database or secret value was used or printed.

## Manual cross-device test

1. Deploy this branch to the staging/Render service without changing its WSGI command.
2. Open the same ready tenant’s system page on device A and groups/properties/external-tables on device B.
3. On device A, create or edit an image/card/member/table record/property.
4. Keep device B visible and do not reload either page.
5. Within about 30 seconds, confirm the changed resource appears on device B.
6. Open a private window as a public/member session and confirm no owner-only `/api/users/` request or 403 appears.
7. Hide the device-B tab, make another change, then show it and confirm one immediate ETag check updates it.
8. While editing a form on device B, confirm the page shows an update notice and preserves the form/scroll position.

## Remaining limits

- Updates are near-real-time polling, not instantaneous; the normal delay is
  up to about 30 seconds.
- Phase B anonymous/public WebSocket notifications were not started.
- Browser visual and cross-device verification remains pending.
- The existing WSGI command remains the deployed command.
