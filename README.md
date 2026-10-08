# UNI-COT

## Render and tenant provisioning

The Free web service runs shared Django migrations at startup, then starts a
bounded provisioning pass in the background before Gunicorn. Incoming requests
for due, unready tenants can also trigger a throttled provisioning process.
No Render shell, worker, or cron service is required.

Set `DATABASE_URL` to a Supabase session-pooler connection on port 5432 or a
direct Postgres URL. Tenant schema creation and `migrate_schemas` require a
session-capable connection; transaction pooler port 6543 cannot perform this
DDL safely. If the app's runtime `DATABASE_URL` must use port 6543, set
`PROVISIONING_DATABASE_URL` to the Supabase session pooler (5432) or direct
connection URL. That URL is selected only by `migrate_schemas`,
`process_provisioning_jobs`, and `provision_tenants`; normal web queries keep
using `DATABASE_URL`. The startup command refuses DDL over port 6543 when no
provisioning URL is configured and logs a warning without printing a URL.

Render runs `collectstatic` during build. It runs `migrate_schemas --shared`
and `process_provisioning_jobs --once` at each web-service start. Provisioning
errors are persisted for retry and do not block the web server from starting.
