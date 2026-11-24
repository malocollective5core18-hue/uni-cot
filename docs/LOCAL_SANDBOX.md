# Local testing sandbox

This setup runs Django on your computer and connects it to a **separate
Supabase test project**. It does not deploy to Render. Do not point it at the
production Supabase project or copy production values from `.env`.

## Configure

1. Create a separate Supabase project for testing. Use only synthetic/test data.
2. In that project's connection settings, select the session pooler (port 5432)
   or direct PostgreSQL connection. Tenant schema switching and migrations need
   a session-capable connection; do not use transaction-pooler port 6543.
3. Copy `local-sandbox.env.example` to `.env.sandbox`. The `.env.sandbox` file is
   ignored by Git. Fill in `DATABASE_URL` with the test project's connection
   string and replace `DJANGO_SECRET_KEY` with a newly generated local-only
   value. Do not edit or overwrite an existing `.env`.
4. Install the project dependencies in a virtual environment:

   ```bash
   python -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   ```

`DJANGO_ENV_FILE` explicitly selects `.env.sandbox`; this prevents Django from
loading the default `.env` file. If the selected file is missing, settings fail
closed instead of silently falling back to another environment.

## Initialize and run

Run these commands from the repository root, with the virtual environment
activated:

```bash
DJANGO_ENV_FILE=.env.sandbox python manage.py check
DJANGO_ENV_FILE=.env.sandbox python manage.py migrate_schemas --shared
DJANGO_ENV_FILE=.env.sandbox python manage.py runserver 127.0.0.1:8000
```

The migration command changes the selected database. Before running it, verify
in the Supabase dashboard that the project is the dedicated test project. The
test database role must be allowed to create schemas and tables.

The local debug configuration uses in-memory cache and channel layers, so
`REDIS_URL` is not needed. The web app listens only on loopback and is not
publicly accessible. Optional features that upload or display Cloudinary media
need credentials for a separate test Cloudinary account; do not use production
Cloudinary credentials.

## Automated tests

`python manage.py test` uses `TEST_DATABASE_URL` with the separate local
PostgreSQL test settings. Those settings intentionally reject remote Supabase
hosts. Keep automated database tests on the disposable local PostgreSQL
database named `unicott_test`; the Supabase test project above is for manual
local app exploration only.

## Safety note

An existing tracked migration helper appears to contain a credential-like
literal. Do not reuse it. If it is valid, rotate the credential at its provider
and remove it from source/history through the repository's normal incident
response process.
