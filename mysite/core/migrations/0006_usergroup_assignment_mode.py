from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0005_external_table_record_registration_number')]

    operations = [
        migrations.AddField(
            model_name='usergroup',
            name='assignment_mode',
            field=models.CharField(
                choices=[('normal', 'Normal'), ('restrict', 'Restricted')],
                default='normal',
                max_length=10,
            ),
        ),
    ]
