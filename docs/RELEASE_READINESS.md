# Release Readiness

Date: 2026-09-20

## Verdict

**GO — local release gate passed.** All required verification commands passed
against the local PostgreSQL socket database only (`uni_cot_dev`). No
production database, production environment file, secret, push, or deployment
was used.

## Fixed-test classification

| Test or area | Class | Fix |
| --- | --- | --- |
| HTTP 301 test failures | b — test setup | The test client now uses HTTPS, matching the enabled production HTTPS redirect policy. |
| CSRF API flow | b — test setup | CSRF tests use HTTPS and a same-origin `Referer`; CSRF protection remains enabled. |
| Path tenant state routing | c — real regression | Pending/provisioning/failed path tenants now return public setup/error responses before schema activation; only `ready` routes into the tenant schema. |
| `DatabaseOperationForbidden` | b — test class/setup | Database-probing endpoint coverage uses `TestCase`; the database-free `/healthz/` assertion remains zero-query. No `SimpleTestCase` was broadened with `databases = "__all__"`. |

There are no failing tests in the final matrix.

## Gate evidence

| Gate | Result | Evidence |
| --- | --- | --- |
| Local database safety | PASS | Printed `DATABASE_HOST=(local socket) DATABASE_NAME=uni_cot_dev` before each database test run. |
| `python -m compileall -q .` | PASS | Exit status 0. |
| `python manage.py check` | PASS | “System check identified no issues.” |
| `DJANGO_DEBUG=false python manage.py check --deploy` | PASS with expected warning | Exit status 0; only `security.W009` because the command used an intentionally short placeholder secret. |
| `python manage.py makemigrations --check --dry-run` | PASS | “No changes detected.” |
| `python manage.py test` | PASS | 59 tests passed. |
| `python manage.py test --reverse` | PASS | 59 tests passed. |
| `python manage.py test --shuffle 20260920` | PASS | 59 tests passed. |
| `python manage.py test --shuffle 20260921` | PASS | 59 tests passed. |
| Migration proof | PASS | `customers.tests.PathTenantProvisioningTests.test_existing_tenants_are_marked_ready_by_provisioning_migration` passed. |
| Operational endpoint isolation | PASS | `/healthz/` makes zero database queries and sets no session cookie; `/readyz/` and `/internal/provision-tick/` are handled before tenant routing and are covered on the public test host. |
| Tenant lifecycle isolation | PASS | Pending and provisioning return `202`, failed returns `503`, and none invokes the downstream tenant application; ready retains normal tenant routing. |

## Remaining risks

- This result is local-only. A production deployment must supply a long random
  `DJANGO_SECRET_KEY`, reachable Redis, correct trusted origins/hosts, and a
  PostgreSQL connection appropriate for production.
- The provisioning worker must be running (or the documented Free-tier
  fallback must be deliberately operated), otherwise newly registered owners
  remain safely pending.
- Test output has non-failing warnings for a missing local `staticfiles/`
  directory, unordered external-table pagination, and a naive datetime test
  fixture. They should be cleaned up separately; they did not weaken the gate.

No push or deployment was performed.
