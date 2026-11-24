# UNI-COT

## Render and tenant provisioning

The Free web service runs shared Django migrations at startup, then starts a
bounded provisioning pass in the background before Gunicorn. Incoming requests
for due, unready tenants can also trigger a throttled provisioning process.
No Render shell, worker, or cron service is required.

Set the runtime `DATABASE_URL` to a Supabase session-pooler connection on port
5432 or a direct Postgres URL. Do not use transaction-pooler port 6543 for
tenant web requests: `django-tenants` changes PostgreSQL `search_path` when
switching schemas, and transaction pooling does not preserve session state
between transactions. Tenant schema creation and `migrate_schemas` also require
a session-capable connection. If migrations need a different connection from
the runtime, set `PROVISIONING_DATABASE_URL` to the Supabase session pooler
(5432) or direct connection URL. That URL is selected only by
`migrate_schemas`, `process_provisioning_jobs`, and `provision_tenants`; normal
web queries keep using `DATABASE_URL`. The startup command refuses DDL over
transaction-pooler port 6543 when no provisioning URL is configured and logs a
warning without printing a URL.

Render runs `collectstatic` during build. It runs `migrate_schemas --shared`
and `process_provisioning_jobs --once` at each web-service start. Provisioning
errors are persisted for retry and do not block the web server from starting.

## Isolated PostgreSQL tests

`python manage.py test` loads `mysite.settings_test` and requires a separate
PostgreSQL `TEST_DATABASE_URL`. It never uses the runtime `DATABASE_URL` for
tests, and refuses a test URL that resolves to the same host, port, and database
name as the runtime URL. It accepts only loopback hosts and the `unicott_test`
database name; do not point this setting at Supabase or another remote server.
Django's test runner creates and removes its test database, so the test role
needs permission to create databases, and the test database role needs schema
privileges for `django-tenants` migrations. The Docker sandbox and its setup
commands are documented in [docs/POSTGRES_SANDBOX.md](docs/POSTGRES_SANDBOX.md).

The project pins Django 4.2.11, `django-tenants` 3.5.0, and psycopg2-binary
2.9.9. Django 4.2 supports PostgreSQL 12 and newer. Runtime tenant migrations
use `migrate_schemas --shared` for `public` and `migrate_schemas` for tenant
schemas; the test suite still needs an isolated PostgreSQL server to verify
both schema contexts safely.

An external pinger can also nudge provisioning by making a GET or POST request
to `https://uni-cot-mmt1.onrender.com/internal/run-provisioning/` with the
`X-Provisioning-Token` header (or a `token` query parameter). Configure the
Render environment variable `PROVISIONING_CRON_TOKEN` with the shared token.
The endpoint responds immediately and processes up to three due jobs in a
background process.
