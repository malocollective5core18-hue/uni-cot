from django.core.management.base import BaseCommand
from django.db.models import Avg
from django.utils import timezone
from django_tenants.utils import schema_context

from customers.models import CRTenant, TenantDashboardMetric
from service.models import Comment, Member, OwnerUser


class Command(BaseCommand):
    help = "Refresh public dashboard metric snapshots outside web requests."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, help="Refresh one tenant only.")

    def handle(self, *args, **options):
        tenants = CRTenant.objects.exclude(schema_name="public").order_by("id")
        if options["tenant_id"]:
            tenants = tenants.filter(id=options["tenant_id"])

        for tenant in tenants:
            try:
                with schema_context(tenant.schema_name):
                    owner = OwnerUser.objects.filter(id=tenant.owner_id).first()
                    if not owner:
                        raise RuntimeError("Tenant owner row is missing")
                    comments = Comment.objects.filter(owner=owner)
                    values = {
                        "member_count": Member.objects.filter(owner=owner).count(),
                        "review_count": comments.count(),
                        "pending_review_count": comments.filter(status="pending").count(),
                        "average_rating": comments.aggregate(average=Avg("rating"))["average"],
                        "refreshed_at": timezone.now(),
                    }
                TenantDashboardMetric.objects.update_or_create(tenant=tenant, defaults=values)
            except Exception as error:
                self.stderr.write(self.style.ERROR(f"Tenant id={tenant.id} was not refreshed: {error}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"Refreshed tenant id={tenant.id}."))
