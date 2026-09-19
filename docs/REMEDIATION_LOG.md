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
