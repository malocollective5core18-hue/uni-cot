# Realtime Phase 1 report

Date: 2026-09-20  
Branch: `feat/realtime-updates`  
Scope: Phase 1 infrastructure only — no client file and no business-view event wiring.

## Status

**PARTIAL / infrastructure ready; do not deploy yet.** The tenant-isolated ASGI
transport, channel layer configuration, provisioning runner hook, publishing
primitive, health probe, and offline tests are implemented. Phase 2 client
behavior and Phase 3/4 business-event wiring intentionally remain undone.

## Files changed

- `requirements.txt`
- `gunicorn.conf.py`
- `mysite/asgi.py`
- `mysite/realtime.py`
- `mysite/realtime_routing.py`
- `mysite/mysite/settings.py`, `mysite/settings.py`, `mysite/test_settings.py`
- `mysite/customers/apps.py`, `mysite/customers/provisioning.py`
- `mysite/health.py`, `mysite/tests/test_realtime.py`
- `docs/REALTIME_DESIGN.md`, `docs/DEPLOY_RENDER_FREE.md`, `docs/REMEDIATION_LOG.md`

## Gate results

| Gate | Result | Evidence |
| --- | --- | --- |
| Local database guard | PASS | `DATABASE_HOST=(local socket)`, `DATABASE_NAME=uni_cot_dev`; only disposable `test_uni_cot_dev` was created/dropped. |
| Channels/ASGI dependencies | PASS | Pinned Channels 4.1.0, channels-redis 4.2.0, Uvicorn 0.30.6, and uvicorn-worker 0.2.0 installed in `.venv`. |
| ASGI startup | PASS | `gunicorn mysite.asgi:application -k uvicorn_worker.UvicornWorker --workers 1 ... -c gunicorn.conf.py` started and shut down cleanly on local `127.0.0.1`. |
| Realtime isolation tests | PASS | 12 local tests cover cross-tenant delivery, forged paths/messages, anonymous/stale/other-owner denial, tenant lifecycle denial, origin validation, caps, minimal payload, post-commit debounce, swallowed publish error, readiness probe, and Gunicorn hook. |
| Compile/check/migration drift | PASS | `compileall`, `manage.py check`, `check --deploy`, and `makemigrations --check --dry-run` completed successfully. Deploy check has only expected `security.W009` for the short command-scoped placeholder secret. |
| Full normal suite | PASS | 75 tests completed successfully with local placeholder Redis URL; test settings force in-memory cache/channel layer. |
| Full reverse suite | PASS | 75 tests completed successfully. |
| Shuffle seed 20260922 | PASS | 75 tests completed successfully from a fresh disposable test database. |
| Shuffle seed 20260923 | PASS | 75 tests completed successfully from a fresh disposable test database. |
| 200 authenticated idle sockets / RSS | NOT RUN | No production-like Redis connection or safe authenticated 200-socket benchmark harness was available. Inventing memory numbers would be misleading. |
| HTTP latency before/after ASGI | NOT RUN | The ASGI process startup smoke test passed, but no controlled baseline/load harness exists. |

## Design decisions and constraints

- Physical Channels group names use `t.<tenant_key>.<resource>`. Channels rejects
  colon characters, so this is the validator-safe equivalent of the design's
  logical `t:<tenant_key>:<resource>` notation.
- WebSocket tenant selection uses only the exact path tuple. Query strings,
  headers, and client messages cannot select or subscribe to a tenant.
- Events contain only `v`, `type`, `resource`, and `version`; no row data or PII
  travels through a channel.
- `publish_changed()` is deliberately unused by business views in this phase.
  It schedules only after database commit, coalesces for 300 ms, and logs rather
  than breaks a successful write if the layer fails.
- `CustomersConfig.ready()` no longer starts provisioning. The in-process runner
  starts only from Gunicorn `post_fork` with `TENANT_PROVISION_INPROCESS=true`,
  performs a startup drain, and takes a PostgreSQL advisory lock.
- The deployed Render command remains WSGI. Do not switch it to ASGI until the
  missing 200-socket and latency measurements are run and accepted.

## Remaining risks and decisions needed

- Redis connection limits and Redis restarts can disconnect every socket. Events
  are at-most-once; Phase 2 catch-up requests are required before enabling this
  in the UI.
- In-process provisioning remains serialized and can contend with the one
  Free-tier web worker during long schema migrations.
- Confirm whether to provide a safe local benchmark environment for 200
  authenticated sockets and page-latency comparison. Without that evidence,
  retain the WSGI deployment command.
- Do not deploy this phase until Phase 2 provides client reconnect/fallback
  behavior and Phase 3 publishes the first authorized resources.
