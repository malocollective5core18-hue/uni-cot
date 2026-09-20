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

## Owner and tenant isolation validation (2026-09-19)

- Finding: The final owner, tenant, and member isolation checks were blocked by an overlong test tenant key and a fixture that retained the pending state/session from registration. One provisioning test also asserted the old behavior that rejected pending tenant paths before public page handling.
- Change: Corrected the isolation fixture to use a valid 20-character key, normalize its tenant to ready, and restore the authenticated owner session. Updated the pending-path test to verify public path resolution while owner-only APIs remain protected.
- Files touched: `mysite/core/tests.py`, `mysite/customers/tests.py`, `docs/REMEDIATION_LOG.md`.
- Evidence: `mysite.core.tests.TenantSystemIsolationTests` passed all 8 tests. The focused owner registration/login and provisioning run passed the service suite and reached only the stale pending-path assertion before this test correction; rerun is required after the correction.
- Remaining risk: Full project verification commands remain to be run before deployment.

## Test cache isolation (2026-09-19)

- Finding: Registration tests shared the default cache-backed rate-limit keys, so test order could consume the registration limit before the rollback assertion ran.
- Change: Added test-only settings that force the default cache to `LocMemCache`, plus a reusable per-test cache cleanup mixin for registration, login, external signup, tenant registration, and owner-admin login tests. Production limiter behavior and limits were unchanged.
- Files touched: `manage.py`, `mysite/test_settings.py`, `mysite/tests/helpers.py`, `mysite/core/tests.py`, `mysite/service/tests.py`, `docs/REMEDIATION_LOG.md`.
- Evidence: 49 tests passed in normal, reverse, and two shuffled orders. The previously failing rollback test passes alone and the service module passes all 17 tests. `manage.py check` reports no issues and `makemigrations --check --dry-run` reports no changes.
- Remaining risk: Existing non-failing warnings remain for the missing staticfiles directory, unordered external-table pagination, and naive property datetime input.

## Render free-tier health and worker contract (2026-09-19)

- Finding: Render free-tier deploys need a lightweight, always-available health probe and a dedicated worker for queued tenant provisioning. The app was already serving `healthz/` and `readyz/`, but the repository still lacked the documented contract and explicit coverage for the provisioning tick endpoint.
- Change: Added focused Django coverage for the health and provisioning probes, documented the Render-free deployment model, and kept the in-process wake-up + 120s polling fallback plus the optional `internal/provision-tick/` tokenized safety net.
- Files touched: `mysite/core/tests.py`, `mysite/health.py`, `mysite/customers/provisioning.py`, `mysite/customers/models.py`, `docs/DEPLOY_RENDER_FREE.md`, `docs/REMEDIATION_LOG.md`.
- Evidence: `healthz/` performs zero database queries and sets no session cookie; `readyz/` validates the database and cache before returning `503` when either check fails; `internal/provision-tick/` requires a matching token when enabled and accepts a valid token with HTTP 202.
- Remaining risk: Render free-tier resources are intentionally limited, so Postgres sleep, cold starts, and background worker restrictions must still be monitored in production. The app should remain paired with a dedicated worker service and a UptimeRobot healthcheck for real deployment availability.

## Release gate — tenant lifecycle routing and local verification (2026-09-20)

- Finding: Path-based tenant routing resolved active tenants in `pending`, `provisioning`, and `failed` states, then activated their schemas. This could expose an unfinished workspace or cause a schema-selection failure. The release gate also had stale HTTP/CSRF test setup that did not exercise the enabled HTTPS protections.
- Change: Path routes now return a generic public setup page (`202`) for pending/in-progress tenants and a generic public error page (`503`) for failed tenants before schema activation. Ready tenants retain normal schema routing. Test clients use HTTPS, CSRF tests send a same-origin referrer, and cache-sensitive tests use the test-only LocMemCache isolation mixin.
- Files touched: `mysite/mysite/tenant_middleware.py`, `mysite/customers/tests.py`, `mysite/tests/helpers.py`, `mysite/service/tests.py`, `mysite/core/tests.py`, `docs/RELEASE_READINESS.md`, `docs/PROJECT_STATUS.md`, `docs/REMEDIATION_LOG.md`.
- Evidence: Local PostgreSQL test database only (`DATABASE_HOST=(local socket)`, `DATABASE_NAME=uni_cot_dev`). The focused lifecycle/operational suite passed 27 tests; `PathTenantProvisioningTests.test_existing_tenants_are_marked_ready_by_provisioning_migration` passed. Full suite passed 59 tests in normal, reverse, and shuffled orders with seeds `20260920` and `20260921`. Compile, system, deploy, and migration-drift checks exited 0.
- Remaining risk: The deploy check reports the expected `security.W009` for its intentionally short command-scoped placeholder secret. Non-failing warnings remain for a missing local `staticfiles/` directory, unordered external-table pagination, and a naive datetime fixture. Production deployment, Redis availability, and tenant worker operation still require an environment-specific review.

## Company showcase sections (2026-09-20)

- Finding: The public company pages had no shared, maintainable presentation for the supplied product and collaboration content.
- Change: Added two static, scoped sections immediately before each blog section. They share two partials and one 4.9 KB stylesheet whose palette, type scale, card treatment, shadows, spacing, hover behavior, and focus treatment are extracted from the saved `js-hub-2.html` reference. No page-global styles, data queries, browser fetches, polling, or remote images were added.
- Files touched: `static/css/showcase.css`, `templates/partials/showcase_vision.html`, `templates/partials/showcase_collab.html`, `templates/index.html`, `templates/welcome.html`, `mysite/core/tests.py`, `docs/SHOWCASE_REPORT.md`, `docs/REMEDIATION_LOG.md`.
- Evidence: Focused `CompanyShowcaseTemplateTests` passed for both templates, asserting both sections precede the blog, each required external link has `target="_blank"` plus `noopener noreferrer`, and template rendering performs zero database queries. The full verification results are recorded in the showcase report.
- Remaining risk: The supplied raw-IP HOTBANDO HTTPS endpoint timed out during a read-only reachability check and may have certificate issues. Visual screenshots could not be taken because Playwright is unavailable locally; manual desktop and phone-width review remains required.

## Correctness — member, group, external-table, and property screen integration (2026-09-20)

- Finding: User management represented a member's group in both `User.group_name` and `UserGroupMember`. Several write paths updated only one representation, while the system-index member action sent both writes concurrently. This could leave group counts and screens inconsistent or show a duplicate-membership error. External-table signup re-rendered an old in-memory table, and a public property claim PUT could include owner-managed property fields.
- Change: Centralized group assignment/removal in scoped helpers that synchronize membership rows, the legacy display field, group counts, and former leaders. User create/update/delete, direct membership create/update/delete, and member moves use those helpers. The system-index action now makes one authoritative user-group request rather than two racing requests. Successful external-table signup reloads API data before rendering. Public property claims accept and persist only claim evidence; the client sends only those fields.
- Files touched: `mysite/core/views.py`, `mysite/core/tests.py`, `templates/system_index.html`, `templates/external_tables.html`, `templates/properties.html`, `docs/REMEDIATION_LOG.md`.
- Evidence: `TenantSystemIsolationTests` passed all 10 tests, including creation, move, update, and removal of a member group relationship plus a public claim overwrite attempt. Full local-only verification passed 63 tests. `compileall`, system check, deploy check, and migration-drift check all exited 0 with `DATABASE_HOST=(local socket)` and `DATABASE_NAME=uni_cot_dev`.
- Remaining risk: The three large client templates still need manual owner/member browser testing at the tenant URL. Non-failing existing warnings remain for the missing local `staticfiles/` directory, unordered external-table pagination, and a naive property-date fixture.

## Realtime updates — Phase 0 design (2026-09-20)

- Finding: Existing pages retain browser-local API results, so another user's committed change is not visible until a reload or periodic poll. The project has ETag cache versions and path tenant routing but no WebSocket routing, channel layer, or socket authorization.
- Change: Documented the constrained Channels/Redis design, minimal no-PII event contract, path-derived tenant resolution, custom-session authorization, connection limits, catch-up behavior, and phased rollout in `docs/REALTIME_DESIGN.md`. The document also records that this checkout has no `gunicorn.conf.py`, so the provisioning hook must be added and verified in Phase 1 rather than assumed.
- Files touched: `docs/REALTIME_DESIGN.md`, `docs/REMEDIATION_LOG.md`.
- Evidence: Design inspection found the existing ETag version source in `core.views._cache_version_key`/`_invalidate_api_cache`, exact path routing and ready-state checks in `mysite.tenant_middleware.TenantMiddleware`, and custom `service_user` session fields in service login flows.
- Remaining risk: No socket server, dependency, or benchmark harness exists yet. Socket memory and ASGI latency measurements are NOT RUN; Phase 1 must provide infrastructure and isolation tests before client or business events are added.

## Realtime updates — Phase 1 infrastructure (2026-09-20)

- Finding: The project had no ASGI WebSocket routing or channel layer. Its in-process tenant provisioner started from `AppConfig.ready()`, which could run during inappropriate command paths, and no Gunicorn fork hook existed.
- Change: Added a Channels ASGI router, origin/session stack, exact ready-tenant resolver, custom-service-session authorization, resource-scoped channel groups, heartbeat/idle handling, access rechecks, rate and socket caps, in-memory test layer, Redis production layer, layer readiness probe, and no-PII post-commit publishing primitive. Added `gunicorn.conf.py`; its opt-in post-fork runner takes a PostgreSQL advisory lock and drains due provisioning jobs before polling. Removed the `AppConfig.ready()` runner. No client scripts or business event publishers were added. Channels disallows colon group names, so physical names use the documented equivalent `t.<tenant_key>.<resource>`.
- Files touched: `requirements.txt`, `gunicorn.conf.py`, ASGI/realtime/settings/provisioning/health modules, realtime tests, `docs/REALTIME_DESIGN.md`, `docs/DEPLOY_RENDER_FREE.md`, `docs/REALTIME_REPORT.md`, and this log.
- Evidence: local ASGI Gunicorn/Uvicorn-worker startup passed. The focused realtime suite passed 12 tests. Full local-only suite passed 75 tests in normal, reverse, and shuffled orders (`20260922`, `20260923`); compile, checks, and migration-drift check passed. The deployment check's only warning is expected `security.W009` for a short placeholder secret.
- Remaining risk: 200 authenticated idle-socket RSS and controlled WSGI-vs-ASGI latency are NOT RUN, so the documented deployed command remains WSGI. Redis limits/restarts, at-most-once delivery, and single-worker provisioning contention remain deployment risks. Phase 2 client catch-up and fallback behavior is required before any deployment decision.

## Correctness — public group member display (2026-09-20)

- Finding: `groups.html` loaded group memberships from the public `groups/?include_members=1` API, but resolved each member name through the owner-only `users/` API. A public/member session therefore received group IDs without usable names and displayed `User <id>` placeholders.
- Change: The group API now adds only `display_name` to each already-assigned member summary, using one bulk scoped query. It does not expose email, phone, registration number, or directory fields. The groups page uses that safe summary when the owner-only directory is unavailable; owner sessions retain the full directory view. New members are still unassigned until an owner explicitly selects a group.
- Files touched: `mysite/core/views.py`, `mysite/core/tests.py`, `templates/groups.html`, `docs/REMEDIATION_LOG.md`.
- Evidence: The focused tenant suite passed 11 tests. The new regression test asserts the public group member response has exactly `id`, `user_id`, `is_leader`, `joined_at`, and `display_name`, and executes a bounded 10 queries including django-tenants schema switches, pagination, memberships, and one bulk name lookup.
- Remaining risk: This deliberately displays group member names to visitors of the tenant's public groups page, matching the page's existing intended UI. If names must be private, the product should make the groups page owner/member-authenticated instead.

## Correctness — owner directory access and member polling (2026-09-20)

- Finding: The groups page treated a rejected owner-only `users/` directory request as an empty database. A tenant-path `POST /api/users/` can also be a public member registration, so a successful POST did not establish that the browser held an owner session. Repeated polling amplified the misleading message.
- Change: Added an end-to-end ready-tenant owner regression test: owner-admin login, user creation, and both paginated and unpaginated directory requests return success. The API retains its owner-only GET guard; member/anonymous requests remain forbidden. Groups and system user polling now run at least 30 seconds apart, avoid duplicate resource timers, pause while hidden, back off after temporary errors, and stop with an accurate message on 401/403. A 429 respects `Retry-After` before retrying. Rate-limit client IP now accepts `X-Forwarded-For` only from a configured proxy peer, and JSON rate-limit responses carry `Retry-After`.
- Files touched: `mysite/mysite/rate_limit.py`, `mysite/mysite/settings.py`, `mysite/core/views.py`, `mysite/service/views.py`, `mysite/core/tests.py`, `mysite/service/tests.py`, `templates/groups.html`, `templates/system_index.html`, `docs/DEPLOY_RENDER_FREE.md`, `docs/REMEDIATION_LOG.md`.
- Evidence: Focused local PostgreSQL suite passed 18 tests, including the owner-login/create/list regression, anonymous/member/other-owner guard regressions, trusted/untrusted forwarded-IP checks, and owner-admin rate-limit `429` with `Retry-After: 60`. The complete 81-test suite then passed in normal and reverse order and in shuffled orders with seeds `20260924` and `20260925`; compile, system, deployment-settings, and migration-drift checks completed successfully. The deploy check emits only the expected warning for its command-scoped placeholder secret.
- Remaining risk: The owner’s browser must complete the tenant owner-admin login before accessing the protected directory. Render must set `DJANGO_TRUSTED_PROXY_IPS=127.0.0.1` for its local proxy hop; deployments with a different proxy topology need the actual proxy peer IPs configured rather than trusting client networks.

## Correctness — public system startup and missing slider fallback (2026-09-20)

- Finding: `system_index.html` rendered a stale `static/time_table.jpg` fallback that does not exist in the static tree, causing a 404 before API slider images loaded. It also requested the owner-only user directory during every system-page initialization. Public/member requests correctly received 403, but the client reported that expected access denial as a page-wide loading failure.
- Change: Removed the missing local image fallback and stale legacy image array; the slider starts empty and is populated only from its API records. System startup now loads the user directory only when the server-rendered owner-admin session is authenticated. Public cards, images, groups, registration settings, and navigation continue loading for public/member visitors. Updated stale browser text that referred to MySQL.
- Files touched: `templates/system_index.html`, `mysite/core/tests.py`, `docs/REMEDIATION_LOG.md`.
- Evidence: Template regression coverage asserts no `time_table.jpg` fallback request and verifies the directory startup call is behind the owner-authentication guard. Local `compileall`, system/deployment checks, migration-drift check, and full Django suite passed in normal, reverse, and shuffled orders (`20260926`, `20260927`) using the local `uni_cot_dev` database only.
- Remaining risk: A browser that already has the old deployed HTML will continue requesting the deleted fallback until Render deploys this commit and the user hard-refreshes. The owner directory remains intentionally unavailable to members and public visitors.

## Correctness — system-to-groups tenant data synchronization (2026-09-20)

- Finding: `groups.html` tried to load the owner-only user directory even for public/member sessions. The resulting deliberate `403` looked like missing data. The page did not explain that groups are public summaries while the all-member directory requires the matching tenant owner's session.
- Change: The groups page now receives an explicit server-derived `owner_directory_access` flag. It requests the full directory only for the authenticated owner of the resolved ready tenant; otherwise it loads safe group/member summaries and displays a persistent route back to the tenant system owner sign-in. The tenant path and API prefix remain unchanged. Members are not automatically assigned to a group—owners must choose a group explicitly.
- Files touched: `mysite/core/views.py`, `templates/groups.html`, `mysite/core/tests.py`, `docs/REMEDIATION_LOG.md`.
- Evidence: Focused local PostgreSQL tenant suite passed 14 tests. The new end-to-end test creates a group and assigned user through the tenant system APIs, then proves the owner groups page has directory access and the groups endpoint returns that member's display name. Public, member, and other-tenant-owner directory requests remain forbidden. Compile, system/deployment checks, migration-drift check, and the full local suite passed in normal, reverse, and shuffled orders (`20260928`, `20260929`) against local `uni_cot_dev` only.
- Remaining risk: Render must deploy this branch. Owners should open Groups through the tenant system link (`/t/<slug>/<id>/<key>/groups/`); manually opening `/groups/` uses the public schema and cannot show tenant records.

## Correctness — explicit groups member load states (2026-09-20)

- Finding: A failed private users request was rendered as an empty member directory.
- Change: Added loading, successful-empty, data, and error states; 401/403, 429, and network/server failures now show distinct guidance without clearing prior member data.
- Files touched: `templates/groups.html`, `docs/REMEDIATION_LOG.md`.
- Evidence: Template state branches are source-checked; runtime browser verification remains pending.
- Remaining risk: The owner must still authenticate on the matching tenant path for private directory data.

## Correctness — same-browser tenant synchronization (2026-09-20)

- Finding: Separate owner tabs retained stale users/groups data until a refresh.
- Change: Added a small BroadcastChannel/localStorage tenant resource notifier. Successful owner users/groups mutations publish only `{resource, ts}`; the receiving page refetches its ETagged resource when visible and defers while editing.
- Files touched: `static/js/tenant-sync.js`, `templates/system_index.html`, `templates/groups.html`, `docs/REMEDIATION_LOG.md`.
- Evidence: Static notifier is 1.3 KB; source-level mutation hooks and subscriptions are present. Browser two-tab verification is NOT RUN.
- Remaining risk: Other devices still depend on the existing 30-second polling path until the later WebSocket phase.
- Follow-up: The publisher and user-directory refresh also recognize an owner who authenticates through the in-page owner-admin modal after initial render.

## Correctness — visible-page ETag polling contract (2026-09-20)

- Finding: The system groups resource used a repeating interval and did not perform an immediate ETag check when a hidden tab became visible.
- Change: Groups polling now uses one recursive timeout with a 30-second floor, exponential error backoff, hidden-tab pause, and visible-tab checks. Users/groups refreshes are triggered immediately on visibility return.
- Files touched: `templates/system_index.html`, `templates/groups.html`, `docs/REMEDIATION_LOG.md`.
- Evidence: Source inspection shows no `setInterval` in the users/groups resource schedulers; browser timing verification is NOT RUN.
- Remaining risk: Existing unrelated clock/slider/card animation timers remain intervals; the tenant resource schedulers are the ones converted.

## Verification — owner groups synchronization (2026-09-20)

- Finding: The state, public-load guard, tab notifier, and resource polling needed regression coverage.
- Change: Added template/static assertions covering explicit states, owner-only startup, notifier size, and tab subscriptions.
- Files touched: `mysite/core/tests.py`, `docs/REMEDIATION_LOG.md`.
- Evidence: Local PostgreSQL suite passed 84 tests in normal, reverse, and shuffled orders (seeds `9369198075` and `24680`). Compile, `check`, `check --deploy`, and migration-drift checks passed. Browser two-tab verification is NOT RUN because no browser tool is available.
- Remaining risk: A member registering from another device is visible through ETag polling within about 30 seconds; WebSocket phases remain separate.

## Correctness — public system owner-directory guard (2026-09-20)

- Finding: Public registration completion could call the private users loader because the user table exists in the shared system template.
- Change: `loadUserData` and post-registration refresh now return without requesting `/api/users/` unless the server-provided owner-admin flag is true.
- Files touched: `templates/system_index.html`, `docs/REMEDIATION_LOG.md`.
- Evidence: Existing initialization guard plus the new function guard prevent public/member 403 calls; browser verification remains pending.
- Remaining risk: Owner-admin state is evaluated when the page is rendered; an owner must complete the in-page admin login before private management actions.

## Correctness — public grouped-member visibility (2026-09-20)

- Finding: The public groups page depended on the private owner users endpoint, so visitors saw a 403 and an owner sign-in notice instead of members already assigned to groups. Missing summary fields also rendered as `undefined`.
- Change: Created `fix/groups-public-member-visibility` from `fix/groups-member-display` and merged `fix/groups-owner-data-sync`. The public `include_members=1` group response now includes tenant-scoped name, registration number, phone, group, case, and status for assigned members only. The groups page builds its public member table from those summaries, removes the owner-directory notice, and uses fallbacks for missing values.
- Files touched: `mysite/core/views.py`, `templates/groups.html`, `mysite/core/tests.py`, `docs/REMEDIATION_LOG.md`.
- Evidence: Local PostgreSQL tests passed 84 tests in normal, reverse, and shuffled orders. Focused public groups tests passed. `check` and `makemigrations --check --dry-run` passed; `check --deploy` passed with the expected placeholder-secret warning. Browser verification and production deployment were not run.
- Remaining risk: This intentionally makes contact and case fields public to members assigned to a group. Unassigned registrations remain private. Review the tenant’s privacy policy before deployment.
