import secrets

from django.db import migrations, models


def _new_key(existing_keys, length=20):
    while True:
        candidate = secrets.token_urlsafe(length * 2)[:length]
        candidate = "".join(ch for ch in candidate if ch.isalnum())[:length]
        if len(candidate) < length:
            continue
        if candidate not in existing_keys:
            existing_keys.add(candidate)
            return candidate


def populate_tenant_keys(apps, schema_editor):
    CRTenant = apps.get_model("customers", "CRTenant")
    key_counts = {}
    for key in CRTenant.objects.values_list("tenant_key", flat=True):
        normalized = (key or "").strip()
        if not normalized:
            continue
        key_counts[normalized] = key_counts.get(normalized, 0) + 1

    existing_keys = set()
    for tenant in CRTenant.objects.all():
        current_key = (tenant.tenant_key or "").strip()
        if len(current_key) == 20 and key_counts.get(current_key, 0) == 1:
            existing_keys.add(current_key)

    for tenant in CRTenant.objects.all():
        current_key = (tenant.tenant_key or "").strip()
        if len(current_key) == 20 and key_counts.get(current_key, 0) == 1 and current_key in existing_keys:
            continue
        tenant.tenant_key = _new_key(existing_keys)
        tenant.save(update_fields=["tenant_key"])


def ensure_tenant_key_column(apps, schema_editor):
    CRTenant = apps.get_model("customers", "CRTenant")
    table_name = CRTenant._meta.db_table
    existing_columns = {
        column.name for column in schema_editor.connection.introspection.get_table_description(
            schema_editor.connection.cursor(), table_name
        )
    }
    if "tenant_key" in existing_columns:
        return

    field = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        help_text="20-character path key for tenant URLs",
    )
    field.set_attributes_from_name("tenant_key")
    schema_editor.add_field(CRTenant, field)


def cleanup_postgres_tenant_key_artifacts(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return

    with schema_editor.connection.cursor() as cursor:
        cursor.execute("DROP INDEX IF EXISTS tenants_tenant_key_8182c450_like")
        cursor.execute("DROP INDEX IF EXISTS tenants_tenant_key_8182c450")
        cursor.execute("DROP INDEX IF EXISTS tenants_tenant_key_key")
        cursor.execute(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM pg_constraint
                    WHERE conname = 'tenants_tenant_key_key'
                ) THEN
                    ALTER TABLE tenants DROP CONSTRAINT tenants_tenant_key_key;
                END IF;
            END $$;
            """
        )


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0004_normalize_tenantsubscription_legacy_values"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(ensure_tenant_key_column, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="crtenant",
                    name="tenant_key",
                    field=models.CharField(
                        blank=True,
                        help_text="20-character path key for tenant URLs",
                        max_length=20,
                        null=True,
                    ),
                ),
            ],
        ),
        migrations.RunPython(populate_tenant_keys, migrations.RunPython.noop),
        migrations.RunPython(cleanup_postgres_tenant_key_artifacts, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="crtenant",
            name="tenant_key",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="20-character path key for tenant URLs",
                max_length=20,
                unique=True,
            ),
        ),
    ]
