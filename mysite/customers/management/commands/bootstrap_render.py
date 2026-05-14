import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection

from customers.models import CRTenant, Domain
from customers.models import _generate_unique_tenant_key
from customers.models import uses_path_tenant_routing


class Command(BaseCommand):
    help = "Bootstrap public tenant/domain and optional Django superuser for Render free-tier deploys."

    def debug_tenant_records(self):
        """
        Print tenant rows during build so Render logs show exactly what exists.
        """
        tenants = CRTenant.objects.all().order_by("id")
        self.stdout.write("=== TENANT RECORDS IN DB ===")
        for tenant in tenants:
            self.stdout.write(
                "id={id} | schema_name={schema} | name={name} | subdomain={subdomain} | tenant_key={tenant_key}".format(
                    id=tenant.id,
                    schema=tenant.schema_name,
                    name=tenant.name,
                    subdomain=tenant.subdomain,
                    tenant_key=tenant.tenant_key or "",
                )
            )
        self.stdout.write(f"TOTAL: {tenants.count()} tenant(s)")
        self.stdout.write("============================")

    def ensure_public_owner_user_columns(self):
        """
        Repair known public-schema drift for shared service tables.
        """
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'owner_users'
                  AND column_name = 'phone_number'
                """
            )
            has_phone_number = cursor.fetchone() is not None

            if not has_phone_number:
                cursor.execute(
                    """
                    ALTER TABLE owner_users
                    ADD COLUMN phone_number varchar(32) NOT NULL DEFAULT ''
                    """
                )
                self.stdout.write(self.style.SUCCESS("Added missing owner_users.phone_number column."))
            else:
                self.stdout.write("owner_users.phone_number already exists.")

    def ensure_superuser(self):
        username = (os.getenv("DJANGO_SUPERUSER_USERNAME") or "").strip()
        email = (os.getenv("DJANGO_SUPERUSER_EMAIL") or "").strip()
        password = os.getenv("DJANGO_SUPERUSER_PASSWORD") or ""

        if username and email and password:
            User = get_user_model()
            if User.objects.filter(username=username).exists():
                self.stdout.write(f"Superuser {username} already exists.")
            else:
                User.objects.create_superuser(username=username, email=email, password=password)
                self.stdout.write(self.style.SUCCESS(f"Created superuser {username}."))
        else:
            self.stdout.write("Superuser env vars not fully set; skipping superuser creation.")

    def handle(self, *args, **options):
        self.debug_tenant_records()
        self.ensure_public_owner_user_columns()

        if uses_path_tenant_routing():
            legacy_public_tenants = list(CRTenant.objects.filter(schema_name="public").order_by("id"))
            if legacy_public_tenants:
                for legacy_public_tenant in legacy_public_tenants:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Deleting legacy public tenant id={legacy_public_tenant.id} name={legacy_public_tenant.name}."
                        )
                    )
                    legacy_public_tenant.delete()
                self.stdout.write(
                    self.style.WARNING(
                        f"Removed {len(legacy_public_tenants)} legacy public tenant record(s) for path-routing mode."
                    )
                )
            else:
                self.stdout.write("No legacy public tenant record found for path-routing mode.")

            self.debug_tenant_records()
            self.ensure_superuser()
            return

        public_domain = (
            (os.getenv("PUBLIC_TENANT_DOMAIN") or "").strip().lower()
            or (os.getenv("RENDER_EXTERNAL_HOSTNAME") or "").strip().lower()
        )
        public_name = (os.getenv("PUBLIC_TENANT_NAME") or "Public").strip() or "Public"
        public_subdomain = (os.getenv("PUBLIC_TENANT_SUBDOMAIN") or "public").strip() or "public"

        tenant, tenant_created = CRTenant.objects.get_or_create(
            schema_name="public",
            defaults={
                "name": public_name,
                "subdomain": public_subdomain[:63],
                "is_active": True,
                "is_trial": False,
            },
        )

        updates = []
        if tenant.name != public_name:
            tenant.name = public_name
            updates.append("name")
        if not tenant.subdomain:
            tenant.subdomain = public_subdomain[:63]
            updates.append("subdomain")
        if not tenant.tenant_key:
            tenant.tenant_key = _generate_unique_tenant_key()
            updates.append("tenant_key")
        if not tenant.is_active:
            tenant.is_active = True
            updates.append("is_active")
        if updates:
            tenant.save(update_fields=updates)

        if tenant_created:
            self.stdout.write(self.style.SUCCESS("Created public tenant record."))
        else:
            self.stdout.write("Public tenant record already exists.")

        if public_domain:
            domain, domain_created = Domain.objects.get_or_create(
                domain=public_domain,
                defaults={
                    "tenant": tenant,
                    "is_primary": True,
                },
            )
            domain_updates = []
            if domain.tenant_id != tenant.id:
                domain.tenant = tenant
                domain_updates.append("tenant")
            if not domain.is_primary:
                domain.is_primary = True
                domain_updates.append("is_primary")
            if domain_updates:
                domain.save(update_fields=domain_updates)

            if domain_created:
                self.stdout.write(self.style.SUCCESS(f"Created public domain {public_domain}."))
            else:
                self.stdout.write(f"Public domain {public_domain} already exists.")
        else:
            self.stdout.write("PUBLIC_TENANT_DOMAIN not set; skipping public domain creation.")

        self.debug_tenant_records()
        self.ensure_superuser()
