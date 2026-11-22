-- Timetable persistence for the existing Django schema-per-tenant database.
-- Apply the migration through Django (`python manage.py migrate_schemas`) as
-- the authoritative operation. The founder source table lives in public;
-- create the state table once in every tenant schema.

CREATE TABLE IF NOT EXISTS public.core_foundertimetablesource (
    id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    file varchar(100) NOT NULL,
    original_filename varchar(255) NOT NULL,
    version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
    uploaded_by_id bigint NULL,
    uploaded_at timestamptz NOT NULL DEFAULT now()
);

-- Replace tenant_schema with each existing owner schema name. The JSONB
-- document is scoped by schema, so tenant schedules cannot cross-read/write.
CREATE TABLE IF NOT EXISTS tenant_schema.core_tenanttimetablestate (
    id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    state jsonb NOT NULL DEFAULT '{}'::jsonb,
    source_version integer NOT NULL DEFAULT 0 CHECK (source_version >= 0),
    revision integer NOT NULL DEFAULT 1 CHECK (revision >= 1),
    updated_by_owner_id bigint NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Founder uploads use the project's configured Django FileField storage
-- (currently Cloudinary); `file` stores the storage key, not workbook bytes.
