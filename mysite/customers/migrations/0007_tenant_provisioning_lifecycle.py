from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def mark_existing_tenants_ready(apps, schema_editor):
    CRTenant = apps.get_model("customers", "CRTenant")
    CRTenant.objects.update(provisioning_state="ready")


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0006_crtenant_owner"),
    ]

    operations = [
        migrations.AddField(
            model_name="crtenant",
            name="provisioning_completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="crtenant",
            name="provisioning_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="crtenant",
            name="provisioning_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="crtenant",
            name="provisioning_state",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("provisioning", "Provisioning"),
                    ("ready", "Ready"),
                    ("failed", "Failed"),
                ],
                db_index=True,
                default="pending",
                max_length=20,
            ),
        ),
        migrations.RunPython(mark_existing_tenants_ready, migrations.RunPython.noop),
        migrations.CreateModel(
            name="TenantProvisioningJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("queued", "Queued"), ("running", "Running"), ("retry", "Retry"), ("succeeded", "Succeeded"), ("failed", "Failed")], db_index=True, default="queued", max_length=20)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("locked_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("tenant", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="provisioning_job", to="customers.crtenant")),
            ],
            options={"db_table": "tenant_provisioning_jobs", "ordering": ["next_attempt_at", "id"]},
        ),
    ]
