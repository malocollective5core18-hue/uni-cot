from django.core.management.base import BaseCommand
from django.db.utils import DatabaseError, OperationalError, ProgrammingError
from django_tenants.utils import schema_context

from customers.models import CRTenant
from service.models import Comment, Member, OwnerUser


class Command(BaseCommand):
    help = "List tenant-local member/comment counts for DevOps database visibility checks."

    def add_arguments(self, parser):
        parser.add_argument(
            "--schema",
            dest="schema_name",
            help="Only audit one tenant schema.",
        )

    def handle(self, *args, **options):
        schema_name = options.get("schema_name")
        tenants = CRTenant.objects.select_related("owner").order_by("schema_name")
        if schema_name:
            tenants = tenants.filter(schema_name=schema_name)

        if not tenants.exists():
            self.stdout.write(self.style.WARNING("No tenants matched."))
            return

        self.stdout.write("schema_name | tenant | owner_email | members | comments | pending | note")
        self.stdout.write("-" * 88)

        for tenant in tenants:
            note = ""
            member_count = 0
            comment_count = 0
            pending_count = 0
            owner_email = getattr(tenant.owner, "email", "") or "No owner"

            try:
                with schema_context(tenant.schema_name):
                    tenant_owner = OwnerUser.objects.filter(id=tenant.owner_id).first()
                    if tenant_owner:
                        member_count = Member.objects.filter(owner=tenant_owner).count()
                        comments = Comment.objects.filter(owner=tenant_owner)
                        comment_count = comments.count()
                        pending_count = comments.filter(status="pending").count()
                    else:
                        note = "tenant owner row missing"
            except (DatabaseError, OperationalError, ProgrammingError) as error:
                note = f"schema read failed: {error}"

            self.stdout.write(
                f"{tenant.schema_name} | {tenant.name} | {owner_email} | "
                f"{member_count} | {comment_count} | {pending_count} | {note}"
            )
