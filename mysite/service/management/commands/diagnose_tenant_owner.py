from django.core.management.base import BaseCommand
from customers.models import CRTenant
from service.models import OwnerUser
from django.contrib.auth.hashers import check_password


class Command(BaseCommand):
    help = 'Diagnose tenant-owner relationship for admin login troubleshooting'

    def add_arguments(self, parser):
        parser.add_argument('tenant_subdomain', type=str, help='Tenant subdomain')
        parser.add_argument('tenant_id', type=int, help='Tenant ID')
        parser.add_argument('tenant_key', type=str, help='Tenant key')
        parser.add_argument('owner_email', type=str, help='Owner email')
        parser.add_argument('owner_password', type=str, help='Owner password')

    def handle(self, *args, **options):
        tenant_subdomain = options['tenant_subdomain']
        tenant_id = options['tenant_id']
        tenant_key = options['tenant_key']
        owner_email = options['owner_email']
        owner_password = options['owner_password']

        self.stdout.write("=== TENANT-OWNER DIAGNOSTIC ===")
        self.stdout.write(f"Tenant subdomain: {tenant_subdomain}")
        self.stdout.write(f"Tenant ID: {tenant_id}")
        self.stdout.write(f"Tenant key: {tenant_key}")
        self.stdout.write(f"Owner email: {owner_email}")
        self.stdout.write("")

        # Check if tenant exists
        try:
            tenant = CRTenant.objects.get(
                subdomain=tenant_subdomain,
                id=tenant_id,
                tenant_key=tenant_key,
                is_active=True
            )
            self.stdout.write(
                self.style.SUCCESS(f"✓ Tenant found: {tenant.name}")
            )
            self.stdout.write(f"  - Schema: {tenant.schema_name}")
            self.stdout.write(f"  - Active: {tenant.is_active}")
            self.stdout.write(f"  - Has owner: {tenant.owner is not None}")
        except CRTenant.DoesNotExist:
            self.stdout.write(
                self.style.ERROR("✗ Tenant not found with the specified parameters")
            )
            return

        # Check if owner exists
        try:
            owner = OwnerUser.objects.get(email__iexact=owner_email, is_active=True)
            self.stdout.write(
                self.style.SUCCESS(f"✓ Owner found: {owner.email}")
            )
            self.stdout.write(f"  - Program: {owner.program_name}")
            self.stdout.write(f"  - Active: {owner.is_active}")
        except OwnerUser.DoesNotExist:
            self.stdout.write(
                self.style.ERROR("✗ Owner not found with the specified email")
            )
            return

        # Check if owner is associated with tenant
        if tenant.owner == owner:
            self.stdout.write(
                self.style.SUCCESS("✓ Owner is correctly associated with tenant")
            )
        else:
            self.stdout.write(
                self.style.ERROR("✗ Owner is NOT associated with this tenant")
            )
            if tenant.owner:
                self.stdout.write(f"  - Current tenant owner: {tenant.owner.email}")
            else:
                self.stdout.write("  - Tenant has no owner assigned")
            return

        # Check password
        if check_password(owner_password, owner.password):
            self.stdout.write(
                self.style.SUCCESS("✓ Password matches")
            )
        else:
            self.stdout.write(
                self.style.ERROR("✗ Password does not match")
            )
            return

        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS("🎉 DIAGNOSTIC PASSED: Owner can log in to admin for this tenant")
        )