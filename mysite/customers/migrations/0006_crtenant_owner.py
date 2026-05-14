import django.db.models.deletion
from django.db import migrations, models


def ensure_owner_column(apps, schema_editor):
    CRTenant = apps.get_model("customers", "CRTenant")
    OwnerUser = apps.get_model("service", "OwnerUser")
    table_name = CRTenant._meta.db_table
    existing_columns = {
        column.name for column in schema_editor.connection.introspection.get_table_description(
            schema_editor.connection.cursor(), table_name
        )
    }
    if "owner_id" in existing_columns:
        return

    field = models.OneToOneField(
        OwnerUser,
        on_delete=django.db.models.deletion.CASCADE,
        related_name="tenant",
        null=True,
        blank=True,
    )
    field.set_attributes_from_name("owner")
    schema_editor.add_field(CRTenant, field)


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0005_crtenant_tenant_key"),
        ("service", "0003_owneruser_phone_number"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(ensure_owner_column, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="crtenant",
                    name="owner",
                    field=models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tenant",
                        to="service.owneruser",
                    ),
                ),
            ],
        ),
    ]
