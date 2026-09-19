import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0007_tenant_provisioning_lifecycle"),
    ]

    operations = [
        migrations.CreateModel(
            name="TenantDashboardMetric",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("member_count", models.PositiveIntegerField(default=0)),
                ("review_count", models.PositiveIntegerField(default=0)),
                ("pending_review_count", models.PositiveIntegerField(default=0)),
                ("average_rating", models.DecimalField(blank=True, decimal_places=2, max_digits=4, null=True)),
                ("refreshed_at", models.DateTimeField(blank=True, null=True)),
                ("tenant", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="dashboard_metric", to="customers.crtenant")),
            ],
            options={"db_table": "tenant_dashboard_metrics"},
        ),
    ]
