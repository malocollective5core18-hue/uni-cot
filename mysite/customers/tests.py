from unittest.mock import patch

from django.test import TestCase
from django_tenants.models import TenantMixin

from customers.models import CRTenant, Domain, TenantSubscription, create_owner_tenant
from service.models import OwnerUser


class PathTenantProvisioningTests(TestCase):
    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    def test_create_owner_tenant_skips_domain_creation_in_path_mode(self):
        owner = OwnerUser.objects.create(
            email="owner@example.com",
            program_name="Computer Science",
            password="hashed",
            is_owner=True,
            is_active=True,
        )

        tenant = create_owner_tenant(owner)

        self.assertEqual(tenant.owner, owner)
        self.assertTrue(CRTenant.objects.filter(id=tenant.id).exists())
        self.assertFalse(Domain.objects.filter(tenant=tenant).exists())
        self.assertTrue(TenantSubscription.objects.filter(tenant=tenant, plan="trial").exists())

    @patch.dict("os.environ", {"DJANGO_TENANT_ROUTING_MODE": "path"}, clear=False)
    @patch.object(TenantMixin, "save", side_effect=RuntimeError("tenant mixin should not run"))
    def test_create_owner_tenant_bypasses_tenantmixin_save_in_path_mode(self, mocked_tenant_save):
        owner = OwnerUser.objects.create(
            email="owner2@example.com",
            program_name="Education",
            password="hashed",
            is_owner=True,
            is_active=True,
        )

        tenant = create_owner_tenant(owner)

        self.assertTrue(CRTenant.objects.filter(id=tenant.id).exists())
        mocked_tenant_save.assert_not_called()
