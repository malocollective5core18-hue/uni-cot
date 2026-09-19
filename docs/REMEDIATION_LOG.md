# Remediation Log

## STEP 0 — Baseline (2026-09-19)

- Finding: Baseline required before remediation.
- Change: Created the Python 3.12 virtual environment, installed the pinned requirements, copied the root performance review into this source-of-truth document location, and initialized Git metadata.
- Files touched: `.gitignore`, `docs/PERFORMANCE_REVIEW.md`, `docs/REMEDIATION_LOG.md`, `.git/` (uncommitted Git metadata), `.venv/` (ignored).
- Evidence: `.venv/bin/python --version` reports `Python 3.12.14`; Django reports `4.2.11`. `compileall` and `manage.py check` exited 0. `manage.py check --deploy` exited 0 with one warning caused by the deliberately short, command-scoped baseline secret.
- Remaining risk: `psql -d uni_cot_dev` and Django database operations fail with `FATAL: role "malochief" does not exist`. `makemigrations --check --dry-run` exited 0 but emitted that database-connection warning; the test suite found 13 tests but exited 1 before running them because it could not create the test database. Git has no configured author identity, so the required baseline commit and `perf/bottleneck-remediation` branch have not been created.

### Baseline rerun after local database setup

- Evidence: `psql -d uni_cot_dev` returned `uni_cot_dev | malochief`. `compileall`, `manage.py check`, `manage.py check --deploy`, and `makemigrations --check --dry-run` exited 0. The deploy check warning is solely from the deliberately short command-scoped secret.
- Test result: `manage.py test` ran 13 tests and exited 1 with 9 pre-existing errors. `mysite.service.tests` imports the missing `customers.models.get_public_tenant_domain`; core tenant tests fail while rendering because Cloudinary has no configured `cloud_name`. Django created and destroyed its temporary test database successfully.
- Remaining risk: the test suite is not green at baseline. These failures must be isolated and corrected before any remediation item can be marked verified.

## Founder dashboard hero overflow (2026-09-19)

- Finding: The founder dashboard hero must not expand beyond its page viewport.
- Change: Constrained `.fc-hero` to the available inline width and made its padding/border participate in that width calculation. Removed the explicit `overflow: hidden` rule at the request of the dashboard owner.
- Files touched: `templates/admin_only/founder_SAAS_system_control.html`.
- Evidence: `box-sizing: border-box`, `min-width: 0`, and `max-width: 100%` prevent the hero element itself from producing horizontal overflow in its flex/page container.
- Remaining risk: Browser/device visual testing is still required; the existing baseline suite has unrelated failures (missing Cloudinary test configuration and stale service-test import).

## P0-1 — Per-request session writes (2026-09-19)

- Finding: Read-only requests refreshed and persisted the session on every response, and production could fall back to a per-worker local cache.
- Change: Disabled `SESSION_SAVE_EVERY_REQUEST`, configured cache-backed sessions with an explicit one-week lifetime, and made `REDIS_URL` mandatory outside debug mode. Redis failures are no longer silently treated as cache misses.
- Files touched: `mysite/mysite/settings.py`, `mysite/core/tests.py`.
- Evidence: `SessionConfigurationTests.test_sessions_are_cache_backed_and_not_saved_on_every_request` asserts the configured session backend, alias, expiry, and disabled per-request saves.
- Remaining risk: Redis availability must be supplied and monitored in production. The full suite has pre-existing failures unrelated to this settings change.

## P0-2 — Polling storms (2026-09-19)

- Finding: countdown cards, properties, registration fields, users, and groups repeatedly downloaded full collections every 3–15 seconds.
- Change: Added ETag/`If-None-Match` support to cached JSON responses. Updated the polling clients to retain ETags, accept `304 Not Modified`, and use a 30-second visible-page interval instead of 3, 10, or 15 seconds.
- Files touched: `mysite/core/views.py`, `mysite/core/tests.py`, `templates/system_index.html`, `templates/properties.html`, `templates/groups.html`.
- Evidence: `CachedJsonResponseTests.test_matching_etag_returns_not_modified_without_building_payload` passed; it confirms the response is 304 and skips the payload builder.
- Remaining risk: polling uses a fixed 30-second fallback interval; exponential retry backoff remains to be added before this item can be marked fully fixed.

## P0-3 — Tenant provisioning in web requests (2026-09-19)

- Finding: Tenant signup ran schema creation and migrations during the owner-signup transaction. A disabled synchronous flag only wrote a log line, leaving tenants without a provisioning path.
- Change: Added a durable database-backed provisioning queue and a tenant lifecycle (`pending`, `provisioning`, `ready`, `failed`). New tenants enqueue exactly one provisioning job and are not routable until the worker has completed schema provisioning and its migration-table health check. The `provision_tenants` management command atomically claims jobs, retries failures with bounded exponential delay, and recovers stale worker locks after a crash.
- Files touched: `mysite/customers/models.py`, `mysite/customers/migrations/0007_tenant_provisioning_lifecycle.py`, `mysite/customers/management/commands/provision_tenants.py`, `mysite/customers/tests.py`, `mysite/mysite/tenant_middleware.py`.
- Evidence: focused `customers.tests` provisioning suite covers queued creation, no schema work in the request, a worker success transition, retry state, and rejection of a pending tenant path.
- Remaining risk: production must run one dedicated worker process using `python manage.py provision_tenants`; until it does, newly registered tenants intentionally remain pending rather than receiving unsafe public-schema traffic.

## P0-4 — Cross-schema member login scan (2026-09-19)

- Finding: A member login without a tenant context loaded every active tenant and checked member credentials in each tenant schema.
- Change: Member authentication now requires a resolved tenant workspace. The login helper returns immediately for a public/no-tenant request and only opens the one resolved tenant schema; public member login retains its established tenant-URL guidance.
- Files touched: `mysite/service/views.py`, `mysite/service/tests.py`.
- Evidence: `CrossDeviceLoginTests.test_public_member_login_lookup_does_not_query_tenants` asserts that a public-context lookup executes zero database queries.
- Remaining risk: Members must use their tenant URL (the intended existing flow). A future public identity registry could support tenant-independent member login without reintroducing cross-schema scans.

## Test blocker — Public tenant host helper (2026-09-19)

- Finding: `service.tests` imported a removed `customers.models.get_public_tenant_domain` helper, preventing the whole test module from loading.
- Change: Restored the helper with explicit URL parsing so a configured public host is returned without scheme, port, or path.
- Files touched: `mysite/customers/models.py`.
- Evidence: `TenantDomainConfigTests.test_public_tenant_domain_strips_protocol` exercises the required normalization.
- Remaining risk: Database-backed test execution is still required.

## P0-5 — Founder dashboard tenant fan-out (2026-09-19)

- Finding: Founder dashboard rendering performed tenant-local aggregation and extra public-schema queries for every tenant.
- Change: Added a public metrics snapshot table and an offline refresh command. The dashboard now loads 25 tenants per page with bulk public metadata, domains, subscriptions, and metric snapshots; it performs no tenant schema switch during rendering.
- Files touched: `mysite/customers/models.py`, `mysite/customers/migrations/0008_tenantdashboardmetric.py`, `mysite/customers/management/commands/refresh_tenant_dashboard_metrics.py`, `mysite/core/views.py`, `templates/admin_only/founder_SAAS_system_control.html`.
- Evidence: `core.tests.FounderDashboardQueryTests` passed with 10 SQL statements for a 25-tenant page: five bounded public data queries plus django-tenants' five required `SET search_path` statements. The count does not grow with tenant count.
- Remaining risk: Production must schedule `python manage.py refresh_tenant_dashboard_metrics` so metrics remain current; snapshots are intentionally eventually consistent.

## P1-2 — External-table record counter scans (2026-09-19)

- Finding: Every external-table record create, update, or delete recalculated `record_count` with a full `COUNT(*)` scan.
- Change: Creates now increment and deletes decrement the stored counter atomically with database `F()` expressions; record updates no longer alter the count. Decrements are floored at zero. Added a reconciliation command for existing drift.
- Files touched: `mysite/core/views.py`, `mysite/core/tests.py`, `mysite/core/management/commands/reconcile_external_table_counts.py`.
- Evidence: Pending focused counter tests.
- Remaining risk: Run `python manage.py reconcile_external_table_counts` once after deployment and periodically as an integrity check.
