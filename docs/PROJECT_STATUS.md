# Project Status

Date: 2026-09-20

## Where the work reached

The project is a Django schema-per-tenant SaaS application using PostgreSQL, Redis, path-based tenant routing, and a tenant provisioning lifecycle.

The main production fixes have been implemented and documented in `docs/REMEDIATION_LOG.md`:

- Owner signup creates the public owner and tenant records without running schema migrations inside the HTTP request.
- New tenants receive a durable provisioning job and start in `pending` state.
- The provisioning worker can move a tenant through `provisioning` to `ready`, with retries and stale-job recovery.
- Tenant routing only exposes a tenant workspace after the tenant is ready.
- Owner login is allowed from the public service page, while member login requires a resolved tenant workspace.
- Cross-schema member login scanning was removed to protect performance and tenant isolation.
- Health, readiness, and provisioning-tick endpoints were added and placed before tenant resolution.
- Redis-backed cache behavior, cache isolation in tests, pagination limits, ETag responses, rate limiting, dashboard metrics, and external-table counter updates were addressed.
- The existing-tenant migration marks existing tenants as `ready`.
- The repository does not intentionally include real credentials or production database settings.

## Release-gate result

The complete local-only verification is green. It used only the local
PostgreSQL socket database `uni_cot_dev`; no production database or secret was
used.

1. `python -m compileall -q .` — passed.
2. `python manage.py check` — passed.
3. `DJANGO_DEBUG=false python manage.py check --deploy` — exited 0; the only
   finding is the expected short-placeholder-secret warning (`security.W009`).
4. `python manage.py makemigrations --check --dry-run` — passed; no changes
   detected.
5. `python manage.py test` — 59 passed.
6. `python manage.py test --reverse` — 59 passed.
7. `python manage.py test --shuffle 20260920` and `--shuffle 20260921` — 59
   passed in each order.
8. `PathTenantProvisioningTests.test_existing_tenants_are_marked_ready_by_provisioning_migration`
   passed.

## Tricky part blocking completion

The difficult part is the interaction between three concerns that must remain correct at the same time:

1. **Tenant isolation:** requests must use the correct tenant schema and must never fall back to another tenant or expose tenant data through the public schema.
2. **Asynchronous provisioning:** a newly registered owner can exist before the tenant schema is ready, so login and routing must distinguish `pending`, `provisioning`, `failed`, and `ready` states.
3. **Order-independent tests:** cache-backed rate limits, session behavior, middleware, schema switching, and test database isolation can make tests pass in one order and fail in another.

A particularly subtle failure mode is that operational endpoints such as `/healthz/`, `/readyz/`, and `/internal/provision-tick/` must bypass tenant resolution. Otherwise a test that is intended to be database-free can trigger tenant middleware and cause forbidden database access before the endpoint handler runs.

Another subtle point is that test setup must not weaken production security. HTTPS redirects, Redis/cache behavior, rate limits, CSRF checks, and tenant readiness checks are production contracts. The correct fix is to isolate test state or use an appropriate database-enabled test class, not to disable those protections globally.

## Current decision

**Status: GO for the local release gate.**

The local verification gate is complete. This is not authorization to deploy:
production environment values, Redis connectivity, the provisioning worker,
and Render resource sizing still need a deployment-specific review. The
complete evidence and residual warnings are in `docs/RELEASE_READINESS.md`.
