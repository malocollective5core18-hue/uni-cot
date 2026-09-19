import os
import secrets
import logging
import time
from datetime import timedelta
from urllib.parse import urlsplit

from django.db import IntegrityError, connection, models, transaction
from django.core.cache import cache
from django.core.management import call_command
from django.utils import timezone
from django.utils.text import slugify
from django_tenants.models import DomainMixin, TenantMixin
from django_tenants.postgresql_backend.base import _check_schema_name

logger = logging.getLogger(__name__)


def _tenant_routing_version_key(tenant_id):
    return f"tenant-routing:version:{tenant_id}"


def invalidate_tenant_routing_cache(tenant_id):
    """Invalidate all cached route variants for one tenant without key scans."""
    if not tenant_id:
        return
    version_key = _tenant_routing_version_key(tenant_id)
    try:
        cache.incr(version_key)
    except ValueError:
        cache.add(version_key, 2, timeout=None)


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


def ensure_tenant_schema_ready(tenant, verbosity=0):
    """
    Ensure tenant apps/tables exist for the tenant schema.

    Path routing skips TenantMixin.save() schema side effects, so we provision
    the schema explicitly after the tenant record is created.
    """
    start = time.time()
    create_schema = getattr(tenant, "create_schema", None)
    if callable(create_schema):
        result = create_schema(check_if_exists=True, verbosity=verbosity)
        logger.info("ensure_tenant_schema_ready: used tenant.create_schema for %s (%.2fs)", getattr(tenant, 'schema_name', None), time.time() - start)
        return result

    schema_name = getattr(tenant, "schema_name", None)
    if not schema_name:
        raise RuntimeError("Cannot provision tenant schema without schema_name.")

    # Fallback for environments where the mixin helper is unavailable.
    try:
        result = call_command("migrate_schemas", schema_name=schema_name, interactive=False, verbosity=verbosity)
        logger.info("ensure_tenant_schema_ready: migrate_schemas completed for %s (%.2fs)", schema_name, time.time() - start)
        return result
    except Exception:
        logger.exception("ensure_tenant_schema_ready: migrate_schemas failed for %s", schema_name)
        raise


def tenant_schema_is_healthy(tenant):
    """Return whether the tenant schema has Django's migration table."""
    schema_name = getattr(tenant, "schema_name", None)
    if not schema_name:
        return False

    set_schema = getattr(connection, "set_schema", None)
    set_schema_to_public = getattr(connection, "set_schema_to_public", None)
    if not callable(set_schema) or not callable(set_schema_to_public):
        return False

    set_schema(schema_name, include_public=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM django_migrations LIMIT 1")
            cursor.fetchone()
        return True
    finally:
        set_schema_to_public()


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


def _generate_unique_tenant_key(length=20):
    while True:
        candidate = secrets.token_urlsafe(length * 2)[:length]
        candidate = "".join(ch for ch in candidate if ch.isalnum())[:length]
        if len(candidate) == length:
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


def get_public_tenant_domain():
    """Return the configured public host without a scheme, port, or path."""
    configured_domain = (os.getenv("PUBLIC_TENANT_DOMAIN") or "").strip()
    render_domain = (os.getenv("RENDER_EXTERNAL_HOSTNAME") or "").strip()
    raw_domain = configured_domain or render_domain
    if not raw_domain:
        return "localhost"

    parsed = urlsplit(raw_domain if "://" in raw_domain else f"//{raw_domain}")
    return (parsed.hostname or "localhost").lower()


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
    tenant_key = models.CharField(
        max_length=20,
        unique=True,
        db_index=True,
        blank=True,
        help_text="20-character path key for tenant URLs",
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

    # Schema creation belongs to the provisioning worker, never an HTTP request.
    auto_create_schema = False

    PROVISIONING_PENDING = "pending"
    PROVISIONING_IN_PROGRESS = "provisioning"
    PROVISIONING_READY = "ready"
    PROVISIONING_FAILED = "failed"
    PROVISIONING_CHOICES = [
        (PROVISIONING_PENDING, "Pending"),
        (PROVISIONING_IN_PROGRESS, "Provisioning"),
        (PROVISIONING_READY, "Ready"),
        (PROVISIONING_FAILED, "Failed"),
    ]
    provisioning_state = models.CharField(
        max_length=20,
        choices=PROVISIONING_CHOICES,
        default=PROVISIONING_PENDING,
        db_index=True,
    )
    provisioning_error = models.TextField(blank=True)
    provisioning_started_at = models.DateTimeField(null=True, blank=True)
    provisioning_completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tenants"
        verbose_name = "Tenant"
        verbose_name_plural = "Tenants"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        result = super().save(*args, **kwargs)
        invalidate_tenant_routing_cache(self.pk)
        return result

    def delete(self, *args, **kwargs):
        tenant_id = self.pk
        result = super().delete(*args, **kwargs)
        invalidate_tenant_routing_cache(tenant_id)
        return result

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
    def primary_domain_url(self):
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


class TenantProvisioningJob(models.Model):
    """Durable, idempotent queue entry for tenant schema provisioning."""

    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_RETRY = "retry"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_RUNNING, "Running"),
        (STATUS_RETRY, "Retry"),
        (STATUS_SUCCEEDED, "Succeeded"),
        (STATUS_FAILED, "Failed"),
    ]

    tenant = models.OneToOneField(
        CRTenant,
        on_delete=models.CASCADE,
        related_name="provisioning_job",
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "tenant_provisioning_jobs"
        ordering = ["next_attempt_at", "id"]

    def __str__(self):
        return f"Provision {self.tenant.schema_name} ({self.status})"


class TenantDashboardMetric(models.Model):
    """Public-schema snapshot used by the founder dashboard."""

    tenant = models.OneToOneField(
        CRTenant,
        on_delete=models.CASCADE,
        related_name="dashboard_metric",
    )
    member_count = models.PositiveIntegerField(default=0)
    review_count = models.PositiveIntegerField(default=0)
    pending_review_count = models.PositiveIntegerField(default=0)
    average_rating = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    refreshed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "tenant_dashboard_metrics"

    def __str__(self):
        return f"Dashboard metrics for {self.tenant.schema_name}"


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
    trial_start = timezone.now().date()
    trial_end = trial_start + timedelta(days=14)

    tenant = None
    for attempt in range(5):
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
        candidate = CRTenant(
            name=owner.program_name,
            schema_name=schema_name,
            subdomain=subdomain,
            tenant_key=_generate_unique_tenant_key(),
            owner=owner,
            paid_until=trial_end,
            subscription_start=trial_start,
            is_active=True,
            is_trial=True,
        )
        try:
            # A savepoint keeps an outer signup transaction usable after a
            # concurrent unique-key collision.
            with transaction.atomic():
                save_tenant_for_routing_mode(candidate, force_insert=True)
        except IntegrityError:
            if attempt == 4:
                raise
            continue
        tenant = candidate
        break

    if tenant is None:
        raise RuntimeError("Unable to allocate a unique tenant identifier.")

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

    TenantProvisioningJob.objects.create(tenant=tenant)
    logger.info(
        "create_owner_tenant: queued provisioning for tenant id=%s schema=%s",
        tenant.id,
        tenant.schema_name,
    )

    return tenant
