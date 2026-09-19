from django.db import migrations, models


def backfill_unique_registration_numbers(apps, schema_editor):
    """Populate the new column without making legacy duplicate rows invalid."""
    ExternalTableRecord = apps.get_model('core', 'ExternalTableRecord')
    seen = set()

    for record in ExternalTableRecord.objects.order_by('table_id', 'id').iterator():
        data = record.data if isinstance(record.data, dict) else {}
        registration_number = data.get('registration_number', '')
        if not isinstance(registration_number, str):
            continue

        registration_number = registration_number.strip()
        key = (record.table_id, registration_number)
        if not registration_number or key in seen:
            continue

        ExternalTableRecord.objects.filter(pk=record.pk).update(
            registration_number=registration_number
        )
        seen.add(key)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_performance_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='externaltablerecord',
            name='registration_number',
            field=models.TextField(blank=True, default=''),
            preserve_default=False,
        ),
        migrations.RunPython(
            backfill_unique_registration_numbers,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name='externaltablerecord',
            constraint=models.UniqueConstraint(
                condition=models.Q(registration_number__gt=''),
                fields=('table', 'registration_number'),
                name='core_ext_table_reg_uniq',
            ),
        ),
    ]
