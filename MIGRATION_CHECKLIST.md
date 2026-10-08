# Supabase Migration Checklist (Neon → Supabase)

**Status**: Ready for execution  
**Project**: uni-cot (Ring-0 Platform)  
**Current DB**: Neon PostgreSQL  
**Target DB**: Supabase PostgreSQL  
**Scope**: Schema-only migration (no user data)  
**Deployment**: Render (onrender.com)  

---

## Pre-Migration Verification ✅

- [x] Supabase project created and accessible
- [x] Connection credentials obtained
- [x] Neon database accessible (for dump)
- [x] No user data to migrate (schema-only safe)
- [x] Django multi-tenant (django-tenants) compatible with PostgreSQL backends
- [x] Render environment accessible

---

## Migration Steps

### Phase 1: Schema Dump (Local or Neon)

**Timing**: Before switching environment variables

```bash
# Step 1a: Dump schema from Neon
# Run this locally or on a machine with psql access

pg_dump \
  --schema-only \
  --no-owner \
  --no-privileges \
  "postgresql://neondb_owner:npg_2uBaf5vZkphq@ep-raspy-mountain-aysrt04o-pooler.c-5.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require" \
  > neon_schema_$(date +%Y%m%d_%H%M%S).sql

# Expected: File size > 100KB (contains all schema definitions)
ls -lh neon_schema_*.sql
```

**What this does**:
- Exports all tables, indexes, constraints, sequences from Neon
- Does NOT export any data (rows)
- Does NOT include user/role definitions
- Safe to run anytime without locking production

---

### Phase 2: Schema Restore (Supabase)

**Timing**: After dump completes successfully

```bash
# Step 2a: Restore schema to Supabase
# Use the DIRECT connection (port 5432) for restoration

psql \
  postgresql://postgres:malocollective%20unicot@db.edjwopbdgxyipxrrscle.supabase.co:5432/postgres \
  -f neon_schema_*.sql

# Expected output: CREATE TABLE, CREATE INDEX messages (no errors)
```

**What this does**:
- Imports all table structures into Supabase public schema
- Creates all indexes and constraints
- Does NOT change any app code or configuration yet

---

### Phase 3: Verify Supabase Schema

**Timing**: After restore, before updating app environment

```bash
# Step 3a: Connect to Supabase and verify tables exist

psql postgresql://postgres:malocollective%20unicot@db.edjwopbdgxyipxrrscle.supabase.co:5432/postgres

# Inside psql, run:
\dt
# Should list all tables from schema (customers, service, core, auth, etc.)

# Count tables
SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';
# Should match Neon table count

# Exit
\q
```

**Verify in Supabase Console**:
1. Open [Supabase Dashboard](https://supabase.com/dashboard)
2. Select your project
3. Go to **SQL Editor**
4. Query: `SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'public';`
5. Should show same number of tables as Neon

---

### Phase 4: Update Render Environment

**Timing**: After Supabase schema verified

This is the LIVE SWITCH. Do this carefully:

1. Go to **Render Dashboard** → Your project
2. Click **Environment** → **Environment Variables**
3. Find `DATABASE_URL` and click **Edit**

**Old value** (Neon):
```
postgresql://neondb_owner:npg_2uBaf5vZkphq@ep-raspy-mountain-aysrt04o-pooler.c-5.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require
```

**New value** (Supabase - Connection Pooler):
```
postgresql://postgres:malocollective%20unicot@db.edjwopbdgxyipxrrscle.supabase.co:6543/postgres?sslmode=require
```

✅ **Save** → Render will automatically redeploy

---

### Phase 5: Monitor Deployment

**Timing**: During and after redeploy

```bash
# Monitor Render logs in real-time
# Go to Render Dashboard → Logs tab

# Look for:
# ✅ "Database connection established"
# ✅ "Django migrations applied" (should be 0 new)
# ❌ Any "connection refused" or "authentication failed" errors
```

**Expected logs**:
```
Starting service...
Loading environment variables...
Running migrations... (0 pending)
Starting gunicorn server...
App running on :10000
```

---

### Phase 6: Verify Django Connection

**Timing**: After Render redeploy completes (green status)

**Option A**: Via Render shell

```bash
# SSH into Render instance
render connect <service-name>

# Test Django connection
python manage.py shell
```

In Python shell:
```python
from django.db import connection
connection.ensure_connection()
print("✅ Supabase connection successful!")

# Test tenant model access
from customers.models import CRTenant
print(f"Tenants: {CRTenant.objects.count()}")
```

**Option B**: Via HTTP (if app is running)

```bash
# Curl health endpoint (if you have one)
curl -I https://uni-cot-mmt1.onrender.com/

# Or test via browser
# Visit: https://uni-cot-mmt1.onrender.com/
# Should load without database errors
```

---

### Phase 7: Verify Tenant Schemas

**Timing**: After app is confirmed working

```bash
# Via Supabase SQL Editor or local psql:

# Connect to Supabase (Direct connection, port 5432)
psql postgresql://postgres:malocollective%20unicot@db.edjwopbdgxyipxrrscle.supabase.co:5432/postgres

# List all schemas
\dn

# Should show:
# public (shared data: customers, service, core tables)
# tenant_* (one schema per tenant, if any exist)

# Check if django_tenants migration table exists
SELECT * FROM public.django_tenants_tenant LIMIT 5;

# Exit
\q
```

---

## Rollback Plan (If Something Goes Wrong)

**Timing**: Any time during/after migration

### Quick Rollback (< 5 minutes)

1. Go to Render Environment Variables
2. Revert `DATABASE_URL` to Neon value
3. Click **Save** → Auto-redeploy
4. Monitor logs until green status
5. Verify app loads

```bash
# Verify back on Neon
python manage.py shell -c "from django.db import connection; print(connection.get_autocommit())"
# Should succeed (no connection error)
```

### Data Safety

- ✅ No data was ever deleted or changed
- ✅ Old Neon database is still intact
- ✅ Schema dump file is safe for re-restore if needed

---

## Troubleshooting

### Error: "connection refused"
**Cause**: Supabase host unreachable or firewall blocked  
**Fix**: 
- Verify IP is whitelisted in Supabase (Settings → Database → Network)
- Use port 6543 (Pooler) for app, 5432 (Direct) for admin tasks
- Test: `telnet db.edjwopbdgxyipxrrscle.supabase.co 6543`

### Error: "authentication failed"
**Cause**: Wrong password or username  
**Fix**:
- User is `postgres` (not `postgres.xxxxx`)
- Password is `malocollective unicot` (with space)
- In URLs: encode space as `%20`

### Error: "relation does not exist"
**Cause**: Schema dump didn't restore properly  
**Fix**:
- Verify dump file size: `du -h neon_schema_*.sql` (should be > 100KB)
- Verify restore had no errors in output
- Check public schema tables: `psql ... -c "\dt"`
- Re-restore if needed: delete old schema and re-run restore

### Error: "too many connections"
**Cause**: Connection pooler exhausted (25 connection limit)  
**Fix**:
- Reduce Gunicorn workers in Render settings
- Increase Supabase Connection Pooler limit (Settings → Connection Pooler)
- Restart Render service to reset connections

### Django still shows Neon data
**Cause**: Environment variable not updated or old connection cached  
**Fix**:
- Verify Render env vars actually changed
- Restart Render service (Stop → Start)
- Clear any local caches: `python manage.py shell` → `cache.clear()`

---

## Success Criteria ✅

After migration complete, verify:

- [x] Render deployment is green (running)
- [x] `python manage.py showmigrations` shows 0 pending
- [x] `python manage.py shell` can connect without errors
- [x] `CRTenant.objects.count()` returns expected count
- [x] App homepage loads without 500 errors
- [x] Supabase console shows public schema with all tables
- [x] Tenant schemas exist (if any tenants are created)

---

## Timeline

| Phase | Task | Duration | Owner |
|-------|------|----------|-------|
| 1 | Dump Neon schema | 2-5 min | Local/Admin |
| 2 | Restore to Supabase | 2-5 min | Local/Admin |
| 3 | Verify Supabase | 2 min | Local/Admin |
| 4 | Update Render env vars | 1 min | Render Dashboard |
| 5 | Monitor redeploy | 3-5 min | Render Logs |
| 6 | Test Django connection | 2 min | Django Shell |
| 7 | Verify tenant schemas | 2 min | Supabase SQL |
| **Total** | | **15-25 min** | |

---

## Files in This Branch

- `.env.supabase` — Supabase environment configuration
- `SUPABASE_MIGRATION.md` — Detailed migration guide
- `MIGRATION_CHECKLIST.md` — This file
- `scripts/migrate_to_supabase.sh` — Automated migration script (optional)

---

## Questions?

If you get stuck:

1. Check **Render Logs** for actual error message
2. Test connection locally: `psql postgresql://postgres:...@supabase.co:5432/postgres`
3. Verify Supabase console shows tables
4. Try rolling back to Neon (takes < 5 min)
5. Re-run schema dump/restore if needed

No code changes needed — this is purely a database provider switch. Django is already compatible.

---

**Ready to proceed?** Start with Phase 1 (Schema Dump).
