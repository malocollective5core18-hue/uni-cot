import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from customers.models import CRTenant, Domain


class Command(BaseCommand):
    help = "Bootstrap public tenant/domain and optional Django superuser for Render free-tier deploys."

    def handle(self, *args, **options):
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
