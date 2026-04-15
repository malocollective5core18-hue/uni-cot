import os
from datetime import timedelta

from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django_tenants.models import DomainMixin, TenantMixin
from django_tenants.postgresql_backend.base import _check_schema_name


def get_tenant_routing_mode():
    """
    Resolve how tenants are exposed in URLs.
    """
    return (os.getenv("DJANGO_TENANT_ROUTING_MODE") or "path").strip().lower()


def uses_path_tenant_routing():
    return get_tenant_routing_mode() == "path"


def save_tenant_for_routing_mode(tenant, **kwargs):
    """
    Persist a tenant without schema-side effects when using path routing.
    """
    if uses_path_tenant_routing():
        return models.Model.save(tenant, **kwargs)
    return tenant.save(**kwargs)


def _build_unique_identifier(raw_value, existing_values=None, separator="_"):
    """
    Normalize a user-facing name into a safe schema/subdomain identifier.
    """
    existing_values = set(existing_values or [])
    base_value = slugify(raw_value or "", allow_unicode=False).replace("-", separator)
    base_value = base_value.strip(separator) or "tenant"
    candidate = base_value[:63]
    index = 1

    while candidate in existing_values:
        suffix = f"{separator}{index}"
        candidate = f"{base_value[: max(1, 63 - len(suffix))]}{suffix}"
        index += 1

    return candidate


def get_base_domain():
    """
    Resolve the shared base domain for tenant subdomains in production.
    """
    configured_domain = (os.getenv("PUBLIC_TENANT_DOMAIN") or "").strip().lower()
    render_domain = (os.getenv("RENDER_EXTERNAL_HOSTNAME") or "").strip().lower()
    domain = configured_domain or render_domain

    if not domain:
        return "localhost"

    parts = domain.split(".")
    if len(parts) >= 2:
        return ".".join(parts[1:])
    return domain


class CRTenant(TenantMixin):
    """
    Tenant model for multi-tenancy.
    Each owner gets their own schema and domain mapping.
    """

    name = models.CharField(max_length=100, help_text="Display name for the tenant")
    schema_name = models.CharField(
        max_length=63,
        unique=True,
        db_index=True,
        validators=[_check_schema_name],
    )
    subdomain = models.CharField(
        max_length=63,
        unique=True,
        help_text="Subdomain (e.g., bict)",
    )
    paid_until = models.DateField(
        null=True,
        blank=True,
        help_text="Subscription expiry date",
    )
    subscription_start = models.DateField(
        null=True,
        blank=True,
        help_text="Subscription start date",
    )
    is_active = models.BooleanField(default=True, help_text="Is tenant active?")
    is_trial = models.BooleanField(default=False, help_text="Is trial period?")
    created_on = models.DateField(auto_now_add=True)

    owner = models.OneToOneField(
        "service.OwnerUser",
        on_delete=models.CASCADE,
        related_name="tenant",
        null=True,
        blank=True,
    )

    auto_create_schema = not uses_path_tenant_routing()

    class Meta:
        db_table = "tenants"
        verbose_name = "Tenant"
        verbose_name_plural = "Tenants"

    def __str__(self):
        return self.name

    @property
    def days_remaining(self):
        if not self.paid_until:
            return 0
        return max((self.paid_until - timezone.now().date()).days, 0)

    @property
    def is_subscription_active(self):
        if not self.is_active:
            return False
        if self.paid_until is None:
            return True
        return self.paid_until >= timezone.now().date()

    @property
    def domain_url(self):
        primary_domain = self.domains.filter(is_primary=True).first()
        if primary_domain:
            return primary_domain.domain
        return None


class Domain(DomainMixin):
    """Domain mapping for tenants - supports subdomains"""

    class Meta:
        db_table = "domains"
        verbose_name = "Domain"
        verbose_name_plural = "Domains"

    def __str__(self):
        return self.domain


class TenantSubscription(models.Model):
    STATUS_TRIAL = "trial"
    STATUS_ACTIVE = "active"
    STATUS_GRACE_PERIOD = "grace_period"
    STATUS_PAST_DUE = "past_due"
    STATUS_SUSPENDED = "suspended"
    STATUS_CANCELLED = "cancelled"

    PLAN_CHOICES = [
        ("trial", "Trial"),
        ("basic", "Basic"),
        ("pro", "Pro"),
    ]

    STATUS_CHOICES = [
        (STATUS_TRIAL, "Trial"),
        (STATUS_ACTIVE, "Active"),
        (STATUS_GRACE_PERIOD, "Grace Period"),
        (STATUS_PAST_DUE, "Past Due"),
        (STATUS_SUSPENDED, "Suspended"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    tenant = models.ForeignKey(
        CRTenant,
        on_delete=models.CASCADE,
        related_name="subscriptions",
    )
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default="trial")
    start_date = models.DateField()
    end_date = models.DateField()
    is_active = models.BooleanField(default=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_TRIAL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tenant_subscriptions"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.tenant.name} - {self.plan} ({self.status})"


def resolve_subscription_status(plan, end_date, is_active=True, today=None):
    """
    Resolve a subscription status from the plan and current access window.
    """
    today = today or timezone.now().date()

    if not is_active:
        return TenantSubscription.STATUS_SUSPENDED
    if plan == TenantSubscription.STATUS_TRIAL:
        return TenantSubscription.STATUS_TRIAL
    if end_date and end_date < today:
        return TenantSubscription.STATUS_PAST_DUE
    return TenantSubscription.STATUS_ACTIVE


def create_tenant_subscription(
    tenant,
    *,
    plan="trial",
    status=None,
    duration_days=14,
    start_date=None,
    end_date=None,
    is_active=True,
    deactivate_existing=True,
):
    """
    Create a normalized subscription record for a tenant.
    """
    start_date = start_date or tenant.subscription_start or timezone.now().date()
    end_date = end_date or (start_date + timedelta(days=duration_days))

    if deactivate_existing:
        TenantSubscription.objects.filter(tenant=tenant, is_active=True).update(is_active=False)

    resolved_status = status or resolve_subscription_status(
        plan=plan,
        end_date=end_date,
        is_active=is_active,
    )

    return TenantSubscription.objects.create(
        tenant=tenant,
        plan=plan,
        start_date=start_date,
        end_date=end_date,
        is_active=is_active,
        status=resolved_status,
    )


def create_owner_tenant(owner, base_domain=None):
    """
    Create a tenant, its primary domain, and a starter subscription record.
    """
    existing_schema_names = CRTenant.objects.values_list("schema_name", flat=True)
    existing_subdomains = CRTenant.objects.values_list("subdomain", flat=True)
    schema_name = _build_unique_identifier(
        owner.program_name,
        existing_values=existing_schema_names,
        separator="_",
    )
    subdomain = _build_unique_identifier(
        owner.program_name,
        existing_values=existing_subdomains,
        separator="-",
    )

    trial_start = timezone.now().date()
    trial_end = trial_start + timedelta(days=14)

    tenant = CRTenant(
        name=owner.program_name,
        schema_name=schema_name,
        subdomain=subdomain,
        owner=owner,
        paid_until=trial_end,
        subscription_start=trial_start,
        is_active=True,
        is_trial=True,
    )
    save_tenant_for_routing_mode(tenant, force_insert=True)

    if not uses_path_tenant_routing():
        base_domain = (base_domain or get_base_domain()).strip().lower()
        Domain.objects.create(
            domain=f"{subdomain}.{base_domain}",
            tenant=tenant,
            is_primary=True,
        )

    create_tenant_subscription(
        tenant,
        plan="trial",
        status=TenantSubscription.STATUS_TRIAL,
        start_date=trial_start,
        end_date=trial_end,
        is_active=True,
        deactivate_existing=False,
    )

    return tenant
