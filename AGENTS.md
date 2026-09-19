# AGENTS.md

## Mission
Remove production bottlenecks and misconfigurations from this Django
multi-tenant project (schema-per-tenant, PostgreSQL, Redis, Gunicorn).
Source of truth for findings: `docs/PERFORMANCE_REVIEW.md`.
Track every change in `docs/REMEDIATION_LOG.md`.

## Hard rules
- Read a file before editing it. Search for all callers before removing behavior.
- Small, reviewable diffs. No sweeping rewrites.
- Never touch real credentials, `.env` files, or production data. Never commit secrets.
- Never edit or delete existing migrations. Add new ones only.
- Preserve tenant isolation. If a change could leak data across tenants, stop and explain.
- Keep API response shapes backward compatible unless the task says otherwise.
- Do not add heavy dependencies without justification (Redis client, django-redis,
  Celery/RQ/Django-Q are acceptable).
- Do not invent metrics. If something can't be measured or run, say so.
- Never mark a check as passed if you did not run it.

## Verification (run after every task, report the results)
- `python -m compileall -q .`
- `python manage.py check` and `python manage.py check --deploy`
- `python manage.py makemigrations --check --dry-run`
- `python manage.py test`
- Add or update tests with query-count assertions (`assertNumQueries`) for
  every endpoint you change.

## Conventions
- One task = one branch/PR. Commit message format: `[P0-1] short description`.
- After each task, append to `docs/REMEDIATION_LOG.md`: finding, change, files
  touched, before/after evidence, remaining risk.
- Finish every task with a report: Status (FIXED / PARTIAL / DEFERRED) | Files changed |
  Evidence | Remaining risk | Decisions needed from me.