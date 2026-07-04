from django.db import migrations, models


def ensure_owner_phone_number_column(apps, schema_editor):
    OwnerUser = apps.get_model("service", "OwnerUser")
    table_name = OwnerUser._meta.db_table
    existing_columns = {
        column.name for column in schema_editor.connection.introspection.get_table_description(
            schema_editor.connection.cursor(), table_name
        )
    }
    if "phone_number" in existing_columns:
        return

    field = models.CharField(blank=True, default="", max_length=32)
    field.set_attributes_from_name("phone_number")
    schema_editor.add_field(OwnerUser, field)


class Migration(migrations.Migration):

    dependencies = [
        ("service", "0002_comment_dislikes_comment_likes_comment_rating_reply"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(ensure_owner_phone_number_column, migrations.RunPython.noop),
            ],
            state_operations=[
                migrations.AddField(
                    model_name="owneruser",
                    name="phone_number",
                    field=models.CharField(blank=True, default="", max_length=32),
                ),
            ],
        ),
    ]
