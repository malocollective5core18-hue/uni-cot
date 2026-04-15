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


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0004_normalize_tenantsubscription_legacy_values"),
    ]

    operations = [
        migrations.AddField(
            model_name="crtenant",
            name="tenant_key",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="20-character path key for tenant URLs",
                max_length=20,
                null=True,
            ),
        ),
        migrations.RunPython(populate_tenant_keys, migrations.RunPython.noop),
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
