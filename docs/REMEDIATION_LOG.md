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

## P1-3 — Path tenant lookup overhead (2026-09-19)

- Finding: Every path-routed tenant request queried the tenant registry before application handling.
- Change: Added a 60-second cache for exact, validated path tenant tuples. A per-tenant cache version is bumped on tenant save/delete, invalidating old route entries without cache key scans.
- Files touched: `mysite/customers/models.py`, `mysite/mysite/tenant_middleware.py`, `mysite/customers/tests.py`.
- Evidence: Pending focused cached-resolution test.
- Remaining risk: The legacy host resolver and django-tenants middleware remain active; consolidation requires an integration-tested routing decision and is deliberately not bundled into this cache change.

## P1-4 — Tenant-key generation race (2026-09-19)

- Finding: Tenant-key generation loaded every existing key and could still fail under a concurrent unique-key collision.
- Change: Replaced the full-table key read with fixed-length cryptographic generation. Tenant creation now retries bounded database unique-constraint collisions inside a savepoint, preserving the caller's signup transaction.
- Files touched: `mysite/customers/models.py`, `mysite/customers/tests.py`.
- Evidence: Pending focused collision-retry test.
- Remaining risk: Concurrent identical program names also use the bounded retry path; a sustained collision beyond five attempts is surfaced rather than silently misrouting a tenant.

## P1-1 — Unbounded collection responses (2026-09-19)

- Finding: List APIs accepted up to 1,000 rows and the external-table list endpoint scanned records for every returned table.
- Change: Capped all shared list pagination at 100. Updated user/group/table clients to follow paginated responses. External-table metadata no longer embeds records; clients fetch records through the dedicated paginated records endpoint.
- Files touched: `mysite/core/views.py`, `templates/system_index.html`, `templates/groups.html`, `templates/external_tables.html`.
- Evidence: `core.tests.PaginationTests.test_page_size_is_capped_at_one_hundred` passed. `compileall` and `git diff --check` also passed.
- Remaining risk: The affected screens still assemble all pages in browser memory for their existing all-record interfaces; a follow-up UI redesign should add visible-page rendering and explicit load-more controls.

## P2-1 — Internal exception text in API responses (2026-09-19)

- Finding: Core API exception handlers returned `str(e)` to clients, exposing database, schema, and provider details.
- Change: Replaced unexpected-exception response bodies with one stable generic 500 response. Existing `logger.exception()` calls retain full stack traces in server logs; expected validation and not-found messages are unchanged.
- Files touched: `mysite/core/views.py`, `mysite/core/tests.py`.
- Evidence: `core.tests.ApiErrorResponseTests.test_unexpected_api_error_does_not_include_exception_details` passed. `compileall` and `git diff --check` also passed.
- Remaining risk: Founder HTML form messages and service APIs need a separate error-contract review.

## P2-2 — Session-local rate limiting (2026-09-19)

- Finding: Login and public API abuse limits were stored in browser sessions, allowing cookie resets and adding session writes.
- Change: Replaced both app-local limit helpers with a shared cache-backed fixed-window limiter keyed by scope, client IP, and normalized account identifier where supplied. Login now passes its identifier explicitly; no limiter state is written to the session.
- Files touched: `mysite/mysite/rate_limit.py`, `mysite/core/views.py`, `mysite/service/views.py`, `mysite/core/tests.py`.
- Evidence: `core.tests.SharedRateLimitTests.test_limit_is_not_stored_in_the_session` passed. `compileall` and `git diff --check` also passed.
- Remaining risk: Correct client IP requires Render/proxy forwarding configuration; production Redis remains required by settings.

## Correctness — Concurrent external-table signups (2026-09-19)

- Finding: External-table signup checked JSON data for an existing application and then inserted a row, so two simultaneous requests could both pass the check and create duplicates.
- Change: Added a dedicated `registration_number` column, synchronized from record JSON on every model save, and enforced a conditional PostgreSQL unique constraint on `(table, registration_number)` when a registration number is present. The signup endpoint now converts a race-triggered constraint error into the established duplicate-application response. Owner record create/edit endpoints return a stable validation error on the same constraint. New migration `core.0005` backfills only the first unambiguous legacy value per table; it leaves existing duplicate legacy rows intact and does not delete or rewrite their JSON data.
- Files touched: `mysite/core/models.py`, `mysite/core/migrations/0005_external_table_record_registration_number.py`, `mysite/core/views.py`, `mysite/core/tests.py`.
- Evidence: `compileall`, `manage.py check`, `manage.py check --deploy`, and `makemigrations --check --dry-run` passed (the deploy check reports the expected development-environment security warnings). The focused registration suite applies `core.0005` to a fresh PostgreSQL test database and verifies duplicate rejection, unconstrained non-signup records, and a bounded signup endpoint query count.
- Remaining risk: The full suite ran 43 tests but remains pre-existing non-green: 11 errors require Cloudinary `cloud_name` test configuration, and two service tests have stale response/status expectations. These failures are unrelated to this migration and were not changed here.

## Production incident — Founder dashboard 502 (2026-09-19)

- Finding: A successful founder login redirected to a dashboard request that could never complete when at least one tenant existed. The dashboard iterated over `tenants` while appending each current tenant back into that same list, creating an unbounded loop until the Gunicorn worker timed out or was terminated.
- Change: Removed the erroneous append. Added a dashboard rendering regression test with an authenticated founder, owner, tenant, and dashboard metric.
- Files touched: `mysite/core/views.py`, `mysite/core/tests.py`.
- Evidence: `core.tests.FounderDashboardQueryTests` passed: the rendering test completes with a tenant and the existing bounded-query test remains green.
- Remaining risk: Render must deploy this commit before the public 502 is resolved. The app currently uses one Free-tier Gunicorn worker, so any other slow request can still block concurrent requests; production sizing and worker configuration need a separate deployment task.

## Founder dashboard hero layout (2026-09-19)

- Finding: The positioned hero and its absolutely positioned decorative pseudo-element produced an unwanted visual result.
- Change: Removed `position: relative` from `.fc-hero` and disabled the dependent decorative pseudo-element. The hero retains its width constraints, padding, and requested `overflow: auto` behavior.
- Files touched: `templates/admin_only/founder_SAAS_system_control.html`.
- Evidence: The CSS no longer establishes `.fc-hero` as a positioning context or renders its out-of-flow decorative circle.
- Remaining risk: Visual verification after Render deploy is still required across desktop and mobile widths.

## Free-tier tenant provisioning fallback (2026-09-19)

- Finding: New owner registration safely queued a tenant provisioning job, but the Render Free web service runs Gunicorn only. No worker claimed the job, leaving the tenant `pending` and its owner unable to access its path-routed workspace.
- Change: Added `provision_tenants --drain`, which processes all currently due jobs and exits. Render's existing `bootstrap_render` deploy command now invokes this mode instead of directly creating schemas without updating the provisioning lifecycle. A manual Render deploy therefore provisions queued owners and transitions successful tenants to `ready`.
- Files touched: `mysite/customers/management/commands/provision_tenants.py`, `mysite/customers/management/commands/bootstrap_render.py`, `mysite/customers/tests.py`.
- Evidence: Pending focused test run.
- Remaining risk: This is a Free-tier fallback, not real-time provisioning. A newly registered owner remains pending until the next manual deploy. Paid production should run a dedicated `python manage.py provision_tenants` worker.
