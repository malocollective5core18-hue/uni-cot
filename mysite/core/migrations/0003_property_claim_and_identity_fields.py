from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_user_custom_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='property',
            name='claim_proof',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='property',
            name='claimant_contact',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='property',
            name='claimant_name',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='property',
            name='property_type',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name='property',
            name='registration_number',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
    ]
