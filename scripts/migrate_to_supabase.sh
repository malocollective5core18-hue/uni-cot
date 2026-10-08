#!/bin/bash
# Migration script: Neon PostgreSQL → Supabase PostgreSQL
# Schema-only migration (no user data)

set -e

echo "=========================================="
echo "Neon → Supabase Migration Script"
echo "=========================================="
echo ""

# Supabase credentials
SUPABASE_HOST="db.edjwopbdgxyipxrrscle.supabase.co"
SUPABASE_USER="postgres"
SUPABASE_PASSWORD="malocollective unicot"
SUPABASE_DB="postgres"
SUPABASE_PORT_DIRECT=5432
SUPABASE_PORT_POOLER=6543

# Prompt for Neon credentials
echo "Step 1: Enter your Neon database credentials"
echo "==========================================="
read -p "Neon host (e.g., ec2-xxxxx.compute-1.amazonaws.com): " NEON_HOST
read -p "Neon user (usually 'postgres' or project name): " NEON_USER
read -sp "Neon password: " NEON_PASSWORD
echo ""
read -p "Neon database name: " NEON_DB

echo ""
echo "Step 2: Dumping schema from Neon..."
echo "====================================="

DUMP_FILE="neon_schema_$(date +%Y%m%d_%H%M%S).sql"

PGPASSWORD="$NEON_PASSWORD" pg_dump \
  --schema-only \
  --no-owner \
  --no-privileges \
  -h "$NEON_HOST" \
  -U "$NEON_USER" \
  -d "$NEON_DB" \
  -v \
  > "$DUMP_FILE"

echo "✅ Schema dumped to: $DUMP_FILE"
echo "   File size: $(du -h "$DUMP_FILE" | cut -f1)"
echo ""

echo "Step 3: Restoring schema to Supabase..."
echo "========================================="

PGPASSWORD="$SUPABASE_PASSWORD" psql \
  -h "$SUPABASE_HOST" \
  -U "$SUPABASE_USER" \
  -d "$SUPABASE_DB" \
  -p "$SUPABASE_PORT_DIRECT" \
  -f "$DUMP_FILE" \
  -v

echo "✅ Schema restored to Supabase"
echo ""

echo "Step 4: Generating .env configuration..."
echo "=========================================="

ENV_FILE=".env.supabase"

cat > "$ENV_FILE" << EOF
# Supabase Connection (automatically generated)
# Created: $(date)

# Connection Pooler (Use for Django app runtime)
DATABASE_URL=postgresql://postgres:malocollective%20unicot@${SUPABASE_HOST}:${SUPABASE_PORT_POOLER}/${SUPABASE_DB}

# Individual variables (alternative to DATABASE_URL)
POSTGRES_USER=postgres
POSTGRES_PASSWORD=malocollective unicot
POSTGRES_HOST=${SUPABASE_HOST}
POSTGRES_PORT=${SUPABASE_PORT_POOLER}
POSTGRES_DB=${SUPABASE_DB}
POSTGRES_SSLMODE=require

# Migration complete timestamp
MIGRATION_DATE=$(date)
EOF

echo "✅ Configuration saved to: $ENV_FILE"
echo ""

echo "=========================================="
echo "✅ Migration complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Review the generated .env.supabase file"
echo "2. Copy the DATABASE_URL to your .env file"
echo "3. Run: python manage.py shell"
echo "4. Test: connection.ensure_connection(); print('✅ OK')"
echo ""
echo "Schema dump file: $DUMP_FILE"
echo "Configuration file: $ENV_FILE"
echo ""
echo "Keep both files safe for rollback if needed."
