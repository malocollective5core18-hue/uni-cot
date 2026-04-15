from django.db import migrations


def normalize_subscription_values(apps, schema_editor):
    TenantSubscription = apps.get_model("customers", "TenantSubscription")
    valid_statuses = {
        "trial",
        "active",
        "grace_period",
        "past_due",
        "suspended",
        "cancelled",
    }

    TenantSubscription.objects.filter(plan__in=["premium", "enterprise"]).update(plan="pro")

    for subscription in TenantSubscription.objects.all().only("id", "plan", "status", "is_active", "end_date"):
        new_status = subscription.status

        if subscription.status not in valid_statuses:
            if not subscription.is_active:
                new_status = "suspended"
            elif subscription.plan == "trial":
                new_status = "trial"
            else:
                new_status = "active"

        if new_status != subscription.status:
            subscription.status = new_status
            subscription.save(update_fields=["status"])


class Migration(migrations.Migration):

    dependencies = [
        ("customers", "0003_tenantsubscription_status_and_more"),
    ]

    operations = [
        migrations.RunPython(
            normalize_subscription_values,
            migrations.RunPython.noop,
        ),
    ]
