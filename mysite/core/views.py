import json
import logging
import re
from hashlib import sha256
from datetime import datetime
from time import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login as auth_login, logout as auth_logout
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db import DatabaseError, OperationalError, ProgrammingError, connection, transaction
from django.db.models import Avg, Count, Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from .models import (
    CountdownCard,
    ExternalTable,
    ExternalTableRecord,
    ImagePost,
    Property,
    RegistrationFormField,
    SystemSetting,
    User,
    UserGroup,
    UserGroupMember,
)
from customers.models import (
    CRTenant,
    Domain,
    TenantSubscription,
    create_owner_tenant,
    create_tenant_subscription,
    resolve_subscription_status,
)
from service.models import Comment, Member, OwnerUser


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants  (defined at module level so all views can reference them)
# ---------------------------------------------------------------------------

PUBLIC_DEMO_COUNTDOWN_KEY = "public_demo"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_access_domain(domain):
    if not domain:
        return None
    if settings.DEBUG and domain.endswith('.localhost') and ':' not in domain:
        return f'{domain}:8000'
    return domain


def _rate_limit(request, key, limit=10, window_seconds=60):
    """
    Lightweight session-scoped throttle for public JSON endpoints.
    It is intentionally local so it works without cache infrastructure.
    """
    now = time()
    storage_key = f'rl_{key}'
    timestamps = request.session.get(storage_key, [])
    timestamps = [ts for ts in timestamps if now - ts < window_seconds]
    if len(timestamps) >= limit:
        request.session[storage_key] = timestamps
        return True
    timestamps.append(now)
    request.session[storage_key] = timestamps
    return False


def _tenant_owner_session_error(request):
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.path.startswith('/t/'):
        return JsonResponse({'success': False, 'error': 'Owner login required for this tenant system'}, status=403)
    messages.error(request, 'Please log in as the owner to access this system.')
    return redirect('service:welcome')


def _has_owner_system_access(request):
    tenant = getattr(request, 'tenant', None)
    if not tenant:
        return True

    user = request.session.get('service_user') or {}
    if user.get('user_type') != 'owner':
        return False

    owner_id = user.get('owner_id')
    tenant_owner_id = getattr(tenant, 'owner_id', None)
    if not owner_id or not tenant_owner_id or owner_id != tenant_owner_id:
        return False

    return True


def _ensure_owner_system_access(request):
    if _has_owner_system_access(request):
        return None
    return _tenant_owner_session_error(request)


def _get_product_owner_id(request):
    tenant = getattr(request, 'tenant', None)
    if tenant and getattr(tenant, 'owner_id', None):
        return tenant.owner_id
    return None


def _cache_scope(request):
    tenant = getattr(request, 'tenant', None)
    schema = getattr(tenant, 'schema_name', 'public') or 'public'
    tenant_id = getattr(tenant, 'id', 'public') or 'public'
    owner_id = _get_product_owner_id(request) or 'public'
    return f'{schema}:{tenant_id}:{owner_id}'


def _cache_version_key(request, family):
    return f'ring0:v1:{_cache_scope(request)}:{family}:version'


def _cache_payload_key(request, family):
    version = cache.get(_cache_version_key(request, family), 1)
    query = request.GET.urlencode()
    raw = f'{_cache_scope(request)}:{family}:{version}:{request.path}:{query}'
    digest = sha256(raw.encode('utf-8')).hexdigest()
    return f'ring0:v1:payload:{digest}'


def _cached_json_response(request, family, payload_builder, ttl=None):
    if request.method != 'GET' or request.GET.get('cache') == 'refresh':
        return JsonResponse(payload_builder())

    cache_key = _cache_payload_key(request, family)
    payload = cache.get(cache_key)
    if payload is None:
        payload = payload_builder()
        cache.set(cache_key, payload, ttl or getattr(settings, 'RING0_API_CACHE_TTL', 20))
    return JsonResponse(payload)


def _invalidate_api_cache(request, *families):
    for family in families:
        cache.set(_cache_version_key(request, family), time(), None)


def _tenant_base_path(request):
    tenant = getattr(request, 'tenant', None)
    if (
        tenant
        and getattr(tenant, 'schema_name', None) != 'public'
        and getattr(tenant, 'subdomain', None)
        and getattr(tenant, 'id', None)
        and getattr(tenant, 'tenant_key', None)
    ):
        return f"/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}"
    return ''


def _drop_tenant_schema(schema_name):
    """Drop a tenant schema only after confirming it exists in the catalogue."""
    if not schema_name or schema_name == 'public':
        return

    with connection.cursor() as cursor:
        # Verify the schema actually exists before issuing destructive DDL.
        cursor.execute(
            "SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = %s",
            [schema_name],
        )
        if not cursor.fetchone():
            logger.warning("_drop_tenant_schema: schema %r not found, skipping.", schema_name)
            return
        cursor.execute(
            f'DROP SCHEMA IF EXISTS {connection.ops.quote_name(schema_name)} CASCADE'
        )


def _get_tenant_owner(tenant):
    if not tenant:
        return None

    owner_id = getattr(tenant, 'owner_id', None)
    if not owner_id:
        return None

    with schema_context(getattr(settings, 'PUBLIC_SCHEMA_NAME', 'public')):
        return OwnerUser.objects.filter(id=owner_id, is_active=True).first()


def _get_tenant_data_summary(tenant):
    """
    Read tenant-local service records for founder visibility.
    Django admin/public schema only sees public rows, while signed-up members
    live in each tenant schema by design.
    """
    summary = {
        'member_count': 0,
        'review_count': 0,
        'pending_review_count': 0,
        'avg_rating': None,
        'schema_error': '',
    }

    schema_name = getattr(tenant, 'schema_name', None)
    if not schema_name or schema_name == 'public':
        return summary

    try:
        with schema_context(schema_name):
            tenant_owner = OwnerUser.objects.filter(id=getattr(tenant, 'owner_id', None)).first()
            if not tenant_owner:
                summary['schema_error'] = 'Tenant owner row missing'
                return summary

            comments = Comment.objects.filter(owner=tenant_owner)
            summary.update({
                'member_count': Member.objects.filter(owner=tenant_owner).count(),
                'review_count': comments.count(),
                'pending_review_count': comments.filter(status='pending').count(),
                'avg_rating': comments.aggregate(avg=Avg('rating'))['avg'],
            })
    except Exception as error:
        logger.warning(
            "Could not read tenant-local summary for schema %s: %s",
            schema_name,
            error,
        )
        summary['schema_error'] = str(error)

    return summary


def _scope_users_queryset(request):
    owner_id = _get_product_owner_id(request)
    queryset = User.objects.all()
    if owner_id is not None:
        queryset = queryset.filter(created_by=owner_id)
    return queryset


def _scope_groups_queryset(request):
    owner_id = _get_product_owner_id(request)
    queryset = UserGroup.objects.all()
    if owner_id is not None:
        queryset = queryset.filter(created_by=owner_id)
    return queryset


def _scope_group_members_queryset(request):
    owner_id = _get_product_owner_id(request)
    queryset = UserGroupMember.objects.all()
    if owner_id is not None:
        scoped_user_ids = _scope_users_queryset(request).values_list('id', flat=True)
        scoped_group_ids = _scope_groups_queryset(request).values_list('id', flat=True)
        queryset = queryset.filter(user_id__in=scoped_user_ids, group_id__in=scoped_group_ids)
    return queryset


def _scope_properties_queryset(request):
    owner_id = _get_product_owner_id(request)
    queryset = Property.objects.all()
    if owner_id is not None:
        queryset = queryset.filter(created_by=owner_id)
    return queryset


def _scope_system_settings_queryset(request):
    owner_id = _get_product_owner_id(request)
    queryset = SystemSetting.objects.all()
    if owner_id is not None:
        queryset = queryset.filter(created_by=owner_id)
    else:
        queryset = queryset.filter(created_by__isnull=True)
    return queryset


def _scope_external_tables_queryset(request):
    if getattr(settings, 'USE_TENANT_INFRA', False) and not getattr(request, 'tenant', None):
        return ExternalTable.objects.none()

    owner_id = _get_product_owner_id(request)
    queryset = ExternalTable.objects.all()
    if owner_id is not None:
        queryset = queryset.filter(created_by=owner_id)
    return queryset


def normalize_record(schema, data):
    """
    Normalize record data against current schema.
    Adds missing fields with None if schema defines them.
    Preserves extra fields not in schema (forward compatibility).
    """
    if not isinstance(data, dict):
        data = {}
    if not isinstance(schema, list):
        schema = []

    normalized = data.copy()
    for field in schema:
        field_key = field.get('key', field.get('name', ''))
        if field_key and field_key not in normalized:
            normalized[field_key] = None

    return normalized


def _parse_json_field(value, default):
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, (list, dict)) else default
        except json.JSONDecodeError:
            return default
    return default


def _parse_datetime_input(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except Exception:
            parsed_date = parse_date(raw)
            if parsed_date:
                return datetime.combine(parsed_date, datetime.min.time())
    return None


def _serialize_property(prop):
    return {
        'id': prop.id,
        'item_name': prop.item_name,
        'description': prop.description,
        'category': prop.category,
        'location': prop.location or '',
        'date_found': prop.date_found.isoformat() if prop.date_found else None,
        'image_url': prop.image_url or '',
        'property_type': prop.property_type or '',
        'registration_number': prop.registration_number or '',
        'contact_info': prop.contact_info or '',
        'status': prop.status,
        'claimed_by': prop.claimed_by,
        'claimant_name': prop.claimant_name or '',
        'claimant_contact': prop.claimant_contact or '',
        'claim_proof': prop.claim_proof or '',
        'claimed_at': prop.claimed_at.isoformat() if prop.claimed_at else None,
        'created_at': prop.created_at.isoformat() if prop.created_at else None,
    }


USER_RESERVED_FIELDS = {
    'id', 'uuid', 'full_name', 'name', 'fullName', 'display_name',
    'registration_number', 'registration number', 'reg_no', 'regNo', 'regNumber',
    'email', 'email_address', 'phone', 'phone_number', 'contact_phone',
    'group_name', 'group', 'role', 'status', 'case_info', 'caseInfo',
    'is_verified', 'is_active', 'is_admin', 'created_at', 'updated_at',
    'created_by', 'last_login', 'login_count', 'password_hash',
}


def _extract_user_custom_fields(payload):
    custom_fields = {}
    if not isinstance(payload, dict):
        return custom_fields
    for key, value in payload.items():
        if key in USER_RESERVED_FIELDS or key.startswith('_'):
            continue
        custom_fields[key] = value
    return custom_fields


def _serialize_user(user):
    custom_fields = _parse_json_field(getattr(user, 'custom_fields', {}), {})
    base = {
        'id': user.id,
        'full_name': user.full_name,
        'registration_number': user.registration_number,
        'email': user.email or '',
        'phone': user.phone or '',
        'status': user.status,
        'role': user.role,
        'group_name': user.group_name or '',
        'case_info': getattr(user, 'case_info', '') or '',
        'is_verified': user.is_verified,
        'created_at': user.created_at.isoformat() if user.created_at else None,
        'custom_fields': custom_fields,
    }
    base.update(custom_fields)
    return base


def _serialize_external_table(table):
    return {
        'id': table.id,
        'table_name': table.table_name,
        'fields_schema': table.fields_schema,
        'hidden_columns': table.hidden_columns or [],
        'created_at': table.created_at.isoformat() if table.created_at else None,
        'updated_at': table.updated_at.isoformat() if table.updated_at else None,
        'record_count': table.record_count,
        'is_visible': table.is_visible,
        'is_active': table.is_active,
    }


def _serialize_external_record(record):
    data = record.data
    # JSONField should already return a dict; guard against legacy string storage.
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            data = {}
    return {
        'id': record.id,
        'data': data if isinstance(data, dict) else {},
        'record_data': data if isinstance(data, dict) else {},
        'created_at': record.created_at.isoformat() if record.created_at else None,
        'table_id': record.table_id,
    }


def _serialize_group(group, members=None):
    data = {
        'id': group.id,
        'group_name': group.group_name,
        'group_code': group.group_code or '',
        'description': group.description or '',
        'max_members': group.max_members,
        'current_members': group.current_members,
        'is_flagged': group.is_flagged,
        'leader_id': group.leader_id,
        'created_at': group.created_at.isoformat() if group.created_at else None,
    }
    if members is not None:
        data['members'] = members
    return data


def _sync_external_table_record_count(table):
    table.record_count = ExternalTableRecord.objects.filter(table=table).count()
    table.save(update_fields=['record_count', 'updated_at'])


def _user_schema_error_response(error):
    error_text = str(error)
    logger.exception("User schema/database error: %s", error_text)
    message = 'User data schema is not up to date on this server.'
    if 'custom_fields' in error_text:
        message = (
            'User data schema is missing the custom_fields column. '
            'Run the latest core migrations on the server.'
        )
    return JsonResponse({'success': False, 'error': message}, status=500)


def _paginate_queryset(request, queryset, default_page_size=100):
    """Return a page of results based on ?page= and ?page_size= query params."""
    try:
        page_size = max(1, min(int(request.GET.get('page_size', default_page_size)), 1000))
    except (ValueError, TypeError):
        page_size = default_page_size
    paginator = Paginator(queryset, page_size)
    try:
        page_number = int(request.GET.get('page', 1))
    except (ValueError, TypeError):
        page_number = 1
    page = paginator.get_page(page_number)
    return page, {
        'page': page.number,
        'page_size': page_size,
        'total_pages': paginator.num_pages,
        'total_count': paginator.count,
    }


# ---------------------------------------------------------------------------
# Page views
# ---------------------------------------------------------------------------

@ensure_csrf_cookie
def index(request, *args, **kwargs):
    tenant = getattr(request, 'tenant', None)
    if tenant and getattr(tenant, 'schema_name', None) != 'public':
        tenant_base_path = _tenant_base_path(request)
        if tenant_base_path:
            return redirect(f'{tenant_base_path}/system/')
        return redirect('/system/')
    from service.views import welcome as service_welcome
    return service_welcome(request, *args, **kwargs)


@ensure_csrf_cookie
def groups(request, *args, **kwargs):
    return render(request, 'groups.html', {
        'tenant_base_path': _tenant_base_path(request),
    })


@ensure_csrf_cookie
def properties(request, *args, **kwargs):
    return render(request, 'properties.html', {
        'tenant_base_path': _tenant_base_path(request),
    })


@ensure_csrf_cookie
def external_tables(request, *args, **kwargs):
    framework_fields = []
    if not getattr(settings, 'USE_TENANT_INFRA', False) or getattr(request, 'tenant', None):
        try:
            framework_fields = [{
                'id': field.id,
                'name': field.field_label,
                'key': field.field_key,
                'type': field.field_type,
                'required': 'yes' if field.is_required else 'no',
                'placeholder': field.placeholder or '',
                'options': field.options or '',
                'order': field.display_order,
            } for field in RegistrationFormField.objects.filter(is_active=True).order_by('display_order', 'id')]
        except (DatabaseError, OperationalError, ProgrammingError):
            framework_fields = []

    return render(request, 'external_tables.html', {
        'framework_fields': framework_fields,
        'tenant_base_path': _tenant_base_path(request),
    })


# ---------------------------------------------------------------------------
# Founder / SaaS control panel
# ---------------------------------------------------------------------------

def founder_saas_system_control(request, *args, **kwargs):
    if request.method == 'POST' and request.POST.get('action') == 'founder_login':
        login_identifier = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        user_model = get_user_model()
        founder = user_model.objects.filter(
            Q(email__iexact=login_identifier) | Q(username__iexact=login_identifier)
        ).first()

        if not founder or not founder.is_active or not founder.is_staff or not founder.check_password(password):
            messages.error(request, 'Invalid founder email or password.')
            return render(request, 'admin_only/founder_login.html', status=403)

        auth_login(request, founder)
        messages.success(request, 'Founder access granted.')
        return redirect('founder_saas_system_control')

    if not request.user.is_authenticated or not request.user.is_staff:
        return render(request, 'admin_only/founder_login.html', status=200)

    if request.method == 'POST':
        action = request.POST.get('action', '').strip()
        if action == 'founder_logout':
            auth_logout(request)
            messages.success(request, 'Founder logged out.')
            return redirect('founder_saas_system_control')

        if action == 'create_owner':
            program_name = request.POST.get('program_name', '').strip()
            email = request.POST.get('email', '').strip()
            password = request.POST.get('password', '')
            confirm_password = request.POST.get('confirm_password', '')

            if not program_name or not email or not password:
                messages.error(request, 'Program name, email, and password are required.')
                return redirect('founder_saas_system_control')

            if password != confirm_password:
                messages.error(request, 'Passwords do not match.')
                return redirect('founder_saas_system_control')

            if OwnerUser.objects.filter(email__iexact=email).exists():
                messages.error(request, 'That owner email is already registered.')
                return redirect('founder_saas_system_control')

            try:
                with transaction.atomic():
                    owner = OwnerUser.objects.create(
                        email=email,
                        program_name=program_name,
                        password=make_password(password),
                        is_owner=True,
                        is_active=True,
                    )
                    tenant = create_owner_tenant(owner)
            except Exception as error:
                logger.exception("Founder owner creation failed for %s", email)
                messages.error(request, f'Owner registration failed. Please try again. Error: {error}')
                return redirect('founder_saas_system_control')

            messages.success(request, f'Owner {email} registered for {tenant.name}.')
            return redirect('founder_saas_system_control')

        tenant_id = request.POST.get('tenant_id', '').strip()
        tenant = CRTenant.objects.filter(id=tenant_id).select_related('owner').first()

        if not tenant:
            messages.error(request, 'Tenant not found.')
            return redirect('founder_saas_system_control')

        if action == 'update_access':
            paid_until = parse_date(request.POST.get('paid_until', '').strip())
            plan = request.POST.get('plan', 'basic').strip() or 'basic'
            set_active = request.POST.get('is_active') == 'on'
            mark_trial = request.POST.get('is_trial') == 'on'

            tenant.is_active = set_active
            tenant.is_trial = mark_trial
            tenant.paid_until = paid_until
            if paid_until and not tenant.subscription_start:
                tenant.subscription_start = timezone.now().date()
            tenant.save(update_fields=['is_active', 'is_trial', 'paid_until', 'subscription_start'])

            if paid_until:
                normalized_plan = plan if plan in dict(TenantSubscription.PLAN_CHOICES) else 'basic'
                subscription_status = (
                    TenantSubscription.STATUS_TRIAL
                    if mark_trial
                    else resolve_subscription_status(
                        plan=normalized_plan,
                        end_date=paid_until,
                        is_active=set_active,
                    )
                )
                create_tenant_subscription(
                    tenant,
                    plan=normalized_plan,
                    status=subscription_status,
                    start_date=tenant.subscription_start or timezone.now().date(),
                    end_date=paid_until,
                    is_active=set_active,
                )

            messages.success(request, f'Access settings updated for {tenant.name}.')
            return redirect('founder_saas_system_control')

        if action == 'activate_custom_domain':
            custom_domain = request.POST.get('custom_domain', '').strip().lower()
            make_primary = request.POST.get('make_primary') == 'on'

            if not custom_domain:
                messages.error(request, 'Custom domain is required.')
                return redirect('founder_saas_system_control')

            existing_domain = Domain.objects.filter(domain=custom_domain).select_related('tenant').first()
            if existing_domain and existing_domain.tenant_id != tenant.id:
                messages.error(request, f'{custom_domain} is already assigned to another tenant.')
                return redirect('founder_saas_system_control')

            if make_primary:
                tenant.domains.update(is_primary=False)

            domain, created = Domain.objects.update_or_create(
                domain=custom_domain,
                defaults={'tenant': tenant, 'is_primary': make_primary},
            )

            tenant.is_active = True
            tenant.save(update_fields=['is_active'])

            action_word = 'activated' if created else 'updated'
            messages.success(request, f'Custom domain {custom_domain} {action_word} for {tenant.name}.')
            return redirect('founder_saas_system_control')

        if action == 'delete_owner':
            confirmation = request.POST.get('confirm_owner_email', '').strip()
            owner = _get_tenant_owner(tenant)

            if not owner:
                messages.error(request, 'This tenant does not have an owner account to delete.')
                return redirect('founder_saas_system_control')

            if confirmation.lower() != owner.email.lower():
                messages.error(request, 'Deletion blocked. Confirm with the exact owner email address.')
                return redirect('founder_saas_system_control')

            tenant_name = tenant.name
            owner_email = owner.email
            schema_name = tenant.schema_name

            try:
                with transaction.atomic():
                    _drop_tenant_schema(schema_name)
                    tenant.delete()
                    owner.delete()
            except Exception as error:
                logger.exception("Owner deletion failed for %s", owner_email)
                messages.error(
                    request,
                    f'Owner deletion failed for {owner_email}. Nothing was fully removed. Error: {error}',
                )
                return redirect('founder_saas_system_control')

            messages.success(
                request,
                f'Owner {owner_email} and tenant {tenant_name} were permanently deleted.',
            )
            return redirect('founder_saas_system_control')

        messages.error(request, 'Unknown founder action.')
        return redirect('founder_saas_system_control')

    # GET — dashboard
    total_tenants = CRTenant.objects.count()
    active_tenants = CRTenant.objects.filter(is_active=True).count()
    paid_tenants = CRTenant.objects.filter(
        is_active=True,
        paid_until__isnull=False,
        paid_until__gte=timezone.now().date(),
    ).count()
    expired_tenants = CRTenant.objects.filter(
        Q(is_active=False) | Q(paid_until__lt=timezone.now().date())
    ).distinct().count()
    public_total_reviews = Comment.objects.count()
    public_pending_reviews = Comment.objects.filter(status='pending').count()
    tenant_total_reviews = 0
    tenant_pending_reviews = 0

    tenants = []
    owner_rows = []
    tenant_qs = (
        CRTenant.objects
        .select_related('owner')
        .prefetch_related('domains', 'subscriptions')
        .order_by('-created_on')
    )
    for tenant in tenant_qs:
        owner = _get_tenant_owner(tenant)
        owner_comments = Comment.objects.filter(owner=owner) if owner else Comment.objects.none()
        tenant_summary = _get_tenant_data_summary(tenant)
        tenant.latest_subscription = tenant.subscriptions.filter(is_active=True).order_by('-created_at').first()
        tenant.all_domains = list(tenant.domains.order_by('-is_primary', 'domain'))
        tenant.member_count = tenant_summary['member_count'] or (owner.members.count() if owner else 0)
        tenant.review_count = tenant_summary['review_count'] or owner_comments.count()
        tenant.pending_review_count = tenant_summary['pending_review_count'] or owner_comments.filter(status='pending').count()
        tenant.avg_rating = tenant_summary['avg_rating'] if tenant_summary['avg_rating'] is not None else owner_comments.aggregate(avg=Avg('rating'))['avg']
        tenant.schema_data_error = tenant_summary['schema_error']
        tenant_total_reviews += tenant.review_count
        tenant_pending_reviews += tenant.pending_review_count
        tenant.access_domain = _build_access_domain(tenant.primary_domain_url or tenant.subdomain)
        tenants.append(tenant)
        owner_rows.append({
            'tenant_id': tenant.id,
            'owner_name': tenant.name,
            'owner_email': owner.email if owner else 'No owner email',
            'phone_number': owner.phone_number if owner else '',
            'domain_url': tenant.access_domain,
            'paid_until': tenant.paid_until,
            'days_remaining': tenant.days_remaining,
            'status': 'active' if tenant.is_subscription_active else 'expired',
            'is_active': tenant.is_active,
            'is_trial': tenant.is_trial,
            'plan': (
                tenant.latest_subscription.plan
                if tenant.latest_subscription
                else ('trial' if tenant.is_trial else 'basic')
            ),
        })

    reviews = Comment.objects.select_related('owner', 'owner__tenant', 'member').order_by('-created_at')
    review_signals = (
        Comment.objects.values('owner__program_name')
        .annotate(
            total_reviews=Count('id'),
            pending_reviews=Count('id', filter=Q(status='pending')),
            average_rating=Avg('rating'),
        )
        .order_by('-total_reviews')
    )

    context = {
        'total_tenants': total_tenants,
        'active_tenants': active_tenants,
        'paid_tenants': paid_tenants,
        'expired_tenants': expired_tenants,
        'total_reviews': tenant_total_reviews or public_total_reviews,
        'pending_reviews': tenant_pending_reviews or public_pending_reviews,
        'tenants': tenants,
        'owner_rows': owner_rows,
        'reviews': reviews,
        'review_signals': review_signals,
        'today': timezone.now().date(),
    }
    return render(request, 'admin_only/founder_SAAS_system_control.html', context)


# ---------------------------------------------------------------------------
# API — Slider Images
# ---------------------------------------------------------------------------

def api_slider_images(request, *args, **kwargs):
    """GET: list active images.  POST: create image (owner only)."""
    if request.method == 'GET':
        def build_payload():
            owner_id = _get_product_owner_id(request)
            images = ImagePost.objects.filter(status='active')
            if owner_id is not None:
                images = images.filter(created_by=owner_id)
            data = [{
                'id': img.id,
                'title': img.title,
                'description': img.description or '',
                'image_url': img.cloudinary_url,
                'image_data': img.cloudinary_url,
                'category': img.category,
                'target_url': img.target_url or '',
                'order': img.display_order,
                'status': img.status,
                'expires_at': img.expires_at.isoformat() if img.expires_at else None,
                'created_at': img.created_at.isoformat() if img.created_at else None,
            } for img in images]
            return {'success': True, 'data': data}

        return _cached_json_response(request, 'slider_images', build_payload)

    if request.method == 'POST':
        denied = _ensure_owner_system_access(request)
        if denied:
            return denied
        try:
            body = json.loads(request.body)
            image = ImagePost.objects.create(
                title=body.get('title', ''),
                description=body.get('description', ''),
                cloudinary_url=body.get('cloudinary_url', body.get('image_url', body.get('image_data', ''))),
                cloudinary_public_id=body.get('cloudinary_public_id', ''),
                cloudinary_format=body.get('cloudinary_format', ''),
                category=body.get('category', 'important'),
                display_order=body.get('order', body.get('display_order', 0)),
                target_url=body.get('target_url', ''),
                created_by=_get_product_owner_id(request),
            )
            _invalidate_api_cache(request, 'slider_images')
            return JsonResponse({
                'success': True,
                'message': 'Image created successfully',
                'data': {
                    'id': image.id,
                    'title': image.title,
                    'description': image.description,
                    'image_url': image.cloudinary_url,
                    'category': image.category,
                    'order': image.display_order,
                },
            }, status=201)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.exception("api_slider_images POST error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_slider_image_detail(request, image_id, *args, **kwargs):
    """GET / PUT / DELETE a single image (owner only except GET)."""
    denied = _ensure_owner_system_access(request)
    if denied:
        return denied

    try:
        owner_id = _get_product_owner_id(request)
        image_qs = ImagePost.objects.all()
        if owner_id is not None:
            image_qs = image_qs.filter(created_by=owner_id)
        image = image_qs.get(id=image_id)
    except ObjectDoesNotExist:
        return JsonResponse({'success': False, 'error': 'Image not found'}, status=404)

    if request.method == 'GET':
        return JsonResponse({
            'success': True,
            'data': {
                'id': image.id,
                'title': image.title,
                'description': image.description,
                'image_url': image.cloudinary_url,
                'image_data': image.cloudinary_url,
                'category': image.category,
                'order': image.display_order,
                'status': image.status,
                'created_at': image.created_at.isoformat() if image.created_at else None,
            },
        })

    if request.method == 'PUT':
        try:
            body = json.loads(request.body)
            image.title = body.get('title', image.title)
            image.description = body.get('description', image.description)
            image.cloudinary_url = body.get('cloudinary_url', body.get('image_url', body.get('image_data', image.cloudinary_url)))
            image.cloudinary_public_id = body.get('cloudinary_public_id', image.cloudinary_public_id)
            image.cloudinary_format = body.get('cloudinary_format', image.cloudinary_format)
            image.category = body.get('category', image.category)
            image.display_order = body.get('order', body.get('display_order', image.display_order))
            if 'target_url' in body:
                image.target_url = body['target_url']
            if 'status' in body:
                image.status = body['status']
            image.save()
            _invalidate_api_cache(request, 'slider_images')
            return JsonResponse({
                'success': True,
                'message': 'Image updated successfully',
                'data': {
                    'id': image.id,
                    'title': image.title,
                    'description': image.description,
                    'image_url': image.cloudinary_url,
                    'category': image.category,
                    'order': image.display_order,
                },
            })
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.exception("api_slider_image_detail PUT error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    if request.method == 'DELETE':
        image.delete()
        _invalidate_api_cache(request, 'slider_images')
        return JsonResponse({'success': True, 'message': 'Image deleted successfully'})

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


# ---------------------------------------------------------------------------
# API — Countdown Cards
# ---------------------------------------------------------------------------

def api_countdown_cards(request, *args, **kwargs):
    """GET: list published cards.  POST: create card (owner only)."""
    if request.method == 'GET':
        def build_payload():
            owner_id = _get_product_owner_id(request)
            cards = CountdownCard.objects.filter(status='active', is_published=True)
            if owner_id is not None:
                cards = cards.filter(created_by=str(owner_id))
            else:
                cards = cards.filter(created_by=PUBLIC_DEMO_COUNTDOWN_KEY)
            data = [{
                'id': card.id,
                'title': card.title,
                'description': card.description,
                'file_url': card.file_url,
                'start_time': card.start_time.isoformat() if card.start_time else None,
                'end_time': card.end_time.isoformat() if card.end_time else None,
                'status': card.status,
                'created_by': card.created_by,
                'created_at': card.created_at.isoformat() if card.created_at else None,
            } for card in cards]
            return {'success': True, 'data': data}

        return _cached_json_response(request, 'countdown_cards', build_payload)

    if request.method == 'POST':
        denied = _ensure_owner_system_access(request)
        if denied:
            return denied
        try:
            body = json.loads(request.body)
            start_time = _parse_datetime_input(body.get('start_time'))
            end_time = _parse_datetime_input(body.get('end_time'))
            card = CountdownCard.objects.create(
                title=body.get('title', ''),
                description=body.get('description', ''),
                file_url=body.get('file_url', ''),
                start_time=start_time,
                end_time=end_time,
                created_by=str(_get_product_owner_id(request) or body.get('created_by', PUBLIC_DEMO_COUNTDOWN_KEY)),
            )
            _invalidate_api_cache(request, 'countdown_cards')
            return JsonResponse({
                'success': True,
                'message': 'Countdown card created successfully',
                'data': {
                    'id': card.id,
                    'title': card.title,
                    'description': card.description,
                    'file_url': card.file_url,
                    'start_time': card.start_time.isoformat() if card.start_time else None,
                    'end_time': card.end_time.isoformat() if card.end_time else None,
                },
            }, status=201)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.exception("api_countdown_cards POST error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_countdown_card_detail(request, card_id, *args, **kwargs):
    """GET / PUT / DELETE a single countdown card (owner only)."""
    denied = _ensure_owner_system_access(request)
    if denied:
        return denied

    try:
        owner_id = _get_product_owner_id(request)
        card_qs = CountdownCard.objects.all()
        if owner_id is not None:
            card_qs = card_qs.filter(created_by=str(owner_id))
        else:
            card_qs = card_qs.filter(created_by=PUBLIC_DEMO_COUNTDOWN_KEY)
        card = card_qs.get(id=card_id)
    except ObjectDoesNotExist:
        return JsonResponse({'success': False, 'error': 'Countdown card not found'}, status=404)

    if request.method == 'GET':
        return JsonResponse({
            'success': True,
            'data': {
                'id': card.id,
                'title': card.title,
                'description': card.description,
                'file_url': card.file_url,
                'start_time': card.start_time.isoformat() if card.start_time else None,
                'end_time': card.end_time.isoformat() if card.end_time else None,
                'status': card.status,
                'created_by': card.created_by,
            },
        })

    if request.method == 'PUT':
        try:
            body = json.loads(request.body)
            card.title = body.get('title', card.title)
            card.description = body.get('description', card.description)
            card.file_url = body.get('file_url', card.file_url)
            if 'start_time' in body:
                card.start_time = _parse_datetime_input(body['start_time'])
            if 'end_time' in body:
                card.end_time = _parse_datetime_input(body['end_time'])
            if 'status' in body:
                card.status = body['status']
            if 'is_published' in body:
                card.is_published = body['is_published']
            card.save()
            _invalidate_api_cache(request, 'countdown_cards')
            return JsonResponse({
                'success': True,
                'message': 'Countdown card updated successfully',
                'data': {
                    'id': card.id,
                    'title': card.title,
                    'description': card.description,
                    'file_url': card.file_url,
                    'status': card.status,
                },
            })
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.exception("api_countdown_card_detail PUT error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    if request.method == 'DELETE':
        card.delete()
        _invalidate_api_cache(request, 'countdown_cards')
        return JsonResponse({'success': True, 'message': 'Countdown card deleted successfully'})

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


# ---------------------------------------------------------------------------
# API — Properties (Lost & Found)
# ---------------------------------------------------------------------------

def api_properties(request, *args, **kwargs):
    """GET: list properties.  POST: create property."""
    if request.method == 'GET':
        def build_payload():
            category = request.GET.get('category')
            status = request.GET.get('status')

            properties = _scope_properties_queryset(request)
            if category:
                properties = properties.filter(category=category)
            if status:
                properties = properties.filter(status=status)

            page, meta = _paginate_queryset(request, properties)
            data = [_serialize_property(prop) for prop in page]
            return {'success': True, 'data': data, 'meta': meta}

        return _cached_json_response(request, 'properties', build_payload)

    if request.method == 'POST':
        tenant = getattr(request, 'tenant', None)
        tenant_scoped_submission = bool(tenant and getattr(tenant, 'schema_name', None) not in (None, '', 'public'))

        denied = _ensure_owner_system_access(request)
        if denied and not tenant_scoped_submission:
            return denied
        try:
            body = json.loads(request.body)
            date_found = _parse_datetime_input(body.get('date_found'))
            created_by = _get_product_owner_id(request)
            if created_by is None and tenant_scoped_submission:
                created_by = getattr(tenant, 'owner_id', None)

            prop = Property.objects.create(
                item_name=body.get('item_name', ''),
                description=body.get('description', ''),
                category=body.get('category', 'other'),
                location=body.get('location', ''),
                date_found=date_found,
                image_url=body.get('image_url', ''),
                property_type=body.get('property_type', ''),
                registration_number=body.get('registration_number', ''),
                contact_info=body.get('contact_info', ''),
                reported_by=body.get('reported_by'),
                claimant_name=body.get('claimant_name', ''),
                claimant_contact=body.get('claimant_contact', ''),
                claim_proof=body.get('claim_proof', ''),
                claimed_at=_parse_datetime_input(body.get('claimed_at')),
                status=body.get('status', 'unclaimed'),
                created_by=created_by,
            )
            _invalidate_api_cache(request, 'properties')
            return JsonResponse({
                'success': True,
                'message': 'Property created successfully',
                'data': _serialize_property(prop),
            }, status=201)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.exception("api_properties POST error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_property_detail(request, property_id, *args, **kwargs):
    """GET / PUT / DELETE a single property."""
    try:
        prop = _scope_properties_queryset(request).get(id=property_id)
    except ObjectDoesNotExist:
        return JsonResponse({'success': False, 'error': 'Property not found'}, status=404)

    if request.method == 'GET':
        return JsonResponse({'success': True, 'data': _serialize_property(prop)})

    if request.method == 'PUT':
        try:
            body = json.loads(request.body)
            is_claim_submission = (
                body.get('status') == 'claimed'
                and any(key in body for key in ['claimant_name', 'claimant_contact', 'claim_proof', 'claimed_at'])
            )
            if not is_claim_submission:
                denied = _ensure_owner_system_access(request)
                if denied:
                    return denied

            prop.item_name = body.get('item_name', prop.item_name)
            prop.description = body.get('description', prop.description)
            prop.category = body.get('category', prop.category)
            prop.location = body.get('location', prop.location)
            if 'date_found' in body:
                prop.date_found = _parse_datetime_input(body['date_found'])
            prop.image_url = body.get('image_url', prop.image_url)
            prop.property_type = body.get('property_type', prop.property_type)
            prop.registration_number = body.get('registration_number', prop.registration_number)
            prop.contact_info = body.get('contact_info', prop.contact_info)
            if 'status' in body:
                prop.status = body['status']
            if 'claimed_by' in body:
                prop.claimed_by = body['claimed_by']
            if 'claimant_name' in body:
                prop.claimant_name = body.get('claimant_name') or ''
            if 'claimant_contact' in body:
                prop.claimant_contact = body.get('claimant_contact') or ''
            if 'claim_proof' in body:
                prop.claim_proof = body.get('claim_proof') or ''
            if 'claimed_at' in body:
                prop.claimed_at = _parse_datetime_input(body.get('claimed_at'))
            prop.save()
            _invalidate_api_cache(request, 'properties')
            return JsonResponse({
                'success': True,
                'message': 'Property updated successfully',
                'data': _serialize_property(prop),
            })
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.exception("api_property_detail PUT error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    if request.method == 'DELETE':
        denied = _ensure_owner_system_access(request)
        if denied:
            return denied
        prop.delete()
        _invalidate_api_cache(request, 'properties')
        return JsonResponse({'success': True, 'message': 'Property deleted successfully'})

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


# ---------------------------------------------------------------------------
# API — Users
# ---------------------------------------------------------------------------

def api_users(request, *args, **kwargs):
    """GET: list users.  POST: create user."""
    if request.method == 'GET':
        tenant = getattr(request, 'tenant', None)
        tenant_scoped_read = bool(tenant and getattr(tenant, 'schema_name', None) not in (None, '', 'public'))

        if not _has_owner_system_access(request) and not tenant_scoped_read:
            return JsonResponse({'success': False, 'error': 'Owner login required for this tenant system'}, status=403)
        try:
            def build_payload():
                users = (
                    _scope_users_queryset(request)
                    .filter(is_active=True)
                    .only(
                        'id',
                        'full_name',
                        'registration_number',
                        'email',
                        'phone',
                        'status',
                        'role',
                        'group_name',
                        'case_info',
                        'is_verified',
                        'created_at',
                        'custom_fields',
                    )
                )
                page, meta = _paginate_queryset(request, users)
                data = [_serialize_user(user) for user in page]
                return {'success': True, 'data': data, 'meta': meta}

            return _cached_json_response(request, 'users', build_payload)
        except (DatabaseError, OperationalError, ProgrammingError) as error:
            return _user_schema_error_response(error)

    if request.method == 'POST':
        tenant = getattr(request, 'tenant', None)
        tenant_scoped_submission = bool(tenant and getattr(tenant, 'schema_name', None) not in (None, '', 'public'))

        denied = _ensure_owner_system_access(request)
        if denied and not tenant_scoped_submission:
            return denied
        try:
            try:
                body = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse({'success': False, 'error': 'Invalid JSON in request body'}, status=400)

            owner_id = _get_product_owner_id(request)
            is_public_tenant_signup = denied is not None and tenant_scoped_submission

            if owner_id is None and tenant_scoped_submission:
                owner_id = getattr(tenant, 'owner_id', None)

            if is_public_tenant_signup:
                allowed_keys = {
                    'full_name', 'name', 'fullName', 'display_name',
                    'registration_number', 'reg_no', 'regNo', 'regNumber',
                    'email', 'phone', 'phone_number', 'contact_phone', 'role',
                }
                body = {key: value for key, value in body.items() if key in allowed_keys}
                body['role'] = 'member'
                body.pop('group_name', None)
                body.pop('case_info', None)
                body.pop('status', None)

            # Normalise field name aliases
            if 'full_name' not in body:
                for alias in ('name', 'fullName', 'display_name'):
                    if alias in body:
                        body['full_name'] = body[alias].strip()
                        break
            if 'registration_number' not in body:
                for alias in ('reg_no', 'regNo', 'regNumber'):
                    if alias in body:
                        body['registration_number'] = body[alias].strip()
                        break

            full_name = body.get('full_name', '').strip()
            registration_number = body.get('registration_number', '').strip()

            if not full_name:
                return JsonResponse(
                    {'success': False, 'error': 'full_name is required (provide full_name, name or fullName)'},
                    status=400,
                )
            if not registration_number:
                return JsonResponse(
                    {'success': False, 'error': 'registration_number is required (provide registration_number, reg_no or regNo)'},
                    status=400,
                )

            email = body.get('email', '').strip() or None
            if email and '@' not in email:
                return JsonResponse({'success': False, 'error': 'Invalid email format'}, status=400)

            # Use get_or_create to avoid TOCTOU race on registration_number
            with transaction.atomic():
                existing = _scope_users_queryset(request).filter(registration_number=registration_number).first()
                if existing:
                    return JsonResponse(
                        {'success': False, 'error': 'A user with this registration number already exists'},
                        status=400,
                    )

                # Handle duplicate email: update existing rather than hard-fail
                if email:
                    existing_email_user = (
                        _scope_users_queryset(request)
                        .filter(email=email)
                        .exclude(registration_number=registration_number)
                        .first()
                    )
                    if existing_email_user:
                        existing_email_user.full_name = full_name
                        existing_email_user.registration_number = registration_number
                        existing_email_user.phone = body.get('phone', '').strip() or existing_email_user.phone
                        existing_email_user.role = body.get('role', 'member')
                        existing_custom = _parse_json_field(getattr(existing_email_user, 'custom_fields', {}), {})
                        existing_custom.update(_extract_user_custom_fields(body))
                        existing_email_user.custom_fields = existing_custom
                        existing_email_user.save()
                        _invalidate_api_cache(request, 'users', 'groups')
                        return JsonResponse(
                            {'success': True, 'message': 'User updated (duplicate email used)', 'data': _serialize_user(existing_email_user)},
                            status=200,
                        )

                user = User.objects.create(
                    full_name=full_name,
                    registration_number=registration_number,
                    email=email,
                    phone=body.get('phone', '').strip() or None,
                    role=body.get('role', 'member'),
                    group_name=body.get('group_name', '').strip() or None,
                    case_info=body.get('case_info', '').strip() or None,
                    custom_fields=_extract_user_custom_fields(body),
                    created_by=owner_id,
                )
            _invalidate_api_cache(request, 'users', 'groups')
            return JsonResponse({
                'success': True,
                'message': 'User created successfully',
                'data': _serialize_user(user),
            }, status=201)
        except (DatabaseError, OperationalError, ProgrammingError) as error:
            return _user_schema_error_response(error)
        except Exception as e:
            logger.exception("api_users POST error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_user_detail(request, user_id, *args, **kwargs):
    """GET / PUT / DELETE a single user (owner only)."""
    denied = _ensure_owner_system_access(request)
    if denied:
        return denied
    try:
        user = _scope_users_queryset(request).get(id=user_id)
    except ObjectDoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'}, status=404)

    if request.method == 'GET':
        try:
            return JsonResponse({
                'success': True,
                'data': {
                    **_serialize_user(user),
                    'last_login': user.last_login.isoformat() if user.last_login else None,
                },
            })
        except (DatabaseError, OperationalError, ProgrammingError) as error:
            return _user_schema_error_response(error)

    if request.method == 'PUT':
        try:
            try:
                body = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse({'success': False, 'error': 'Invalid JSON in request body'}, status=400)

            if 'full_name' in body:
                full_name = body['full_name'].strip()
                if not full_name:
                    return JsonResponse({'success': False, 'error': 'full_name cannot be empty'}, status=400)
                user.full_name = full_name

            if 'registration_number' in body:
                registration_number = body['registration_number'].strip()
                if not registration_number:
                    return JsonResponse({'success': False, 'error': 'registration_number cannot be empty'}, status=400)
                if _scope_users_queryset(request).filter(registration_number=registration_number).exclude(id=user_id).exists():
                    return JsonResponse({'success': False, 'error': 'A user with this registration number already exists'}, status=400)
                user.registration_number = registration_number

            if 'email' in body:
                email = body['email'].strip()
                if email and '@' not in email:
                    return JsonResponse({'success': False, 'error': 'Invalid email format'}, status=400)
                email = email or None
                if email and _scope_users_queryset(request).filter(email=email).exclude(id=user_id).exists():
                    return JsonResponse({'success': False, 'error': 'A user with this email already exists'}, status=400)
                user.email = email

            if 'phone' in body:
                user.phone = body['phone'].strip()
            if 'status' in body:
                user.status = body['status']
            if 'role' in body:
                user.role = body['role']
            if 'group_name' in body:
                user.group_name = body['group_name']
            if 'case_info' in body:
                setattr(user, 'case_info', body['case_info'])
            if 'is_verified' in body:
                user.is_verified = body['is_verified']

            custom_fields = _parse_json_field(getattr(user, 'custom_fields', {}), {})
            custom_fields.update(_extract_user_custom_fields(body))
            user.custom_fields = custom_fields
            user.save()
            _invalidate_api_cache(request, 'users', 'groups')
            return JsonResponse({
                'success': True,
                'message': 'User updated successfully',
                'data': _serialize_user(user),
            })
        except (DatabaseError, OperationalError, ProgrammingError) as error:
            return _user_schema_error_response(error)
        except Exception as e:
            logger.exception("api_user_detail PUT error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    if request.method == 'DELETE':
        user.delete()
        _invalidate_api_cache(request, 'users', 'groups')
        return JsonResponse({'success': True, 'message': 'User deleted successfully'})

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


# ---------------------------------------------------------------------------
# API — Groups
# ---------------------------------------------------------------------------

def api_groups(request, *args, **kwargs):
    """GET: list active groups.  POST: create group (owner only)."""
    if request.method == 'GET':
        def build_payload():
            groups = (
                _scope_groups_queryset(request)
                .filter(is_active=True)
                .only(
                    'id',
                    'group_name',
                    'group_code',
                    'description',
                    'max_members',
                    'current_members',
                    'is_flagged',
                    'leader_id',
                    'created_at',
                )
            )
            page, meta = _paginate_queryset(request, groups)
            page_groups = list(page)
            include_members = request.GET.get('include_members') in {'1', 'true', 'yes'}

            members_by_group = {}
            if include_members and page_groups:
                group_ids = [group.id for group in page_groups]
                active_memberships = (
                    _scope_group_members_queryset(request)
                    .filter(group_id__in=group_ids, status='active')
                    .order_by('group_id', 'joined_at')
                )
                for member in active_memberships:
                    members_by_group.setdefault(member.group_id, []).append({
                        'id': member.id,
                        'user_id': member.user_id,
                        'is_leader': member.is_leader,
                        'joined_at': member.joined_at.isoformat() if member.joined_at else None,
                    })

                groups_without_memberships = [
                    group for group in page_groups if not members_by_group.get(group.id)
                ]
                if groups_without_memberships:
                    names_to_ids = {group.group_name: group.id for group in groups_without_memberships}
                    groups_by_id = {group.id: group for group in groups_without_memberships}
                    legacy_users = (
                        _scope_users_queryset(request)
                        .filter(group_name__in=list(names_to_ids.keys()), is_active=True)
                        .only('id', 'group_name')
                        .order_by('group_name', 'id')
                    )
                    for user in legacy_users:
                        group_id = names_to_ids.get(user.group_name)
                        if group_id is None:
                            continue
                        group = groups_by_id.get(group_id)
                        members_by_group.setdefault(group_id, []).append({
                            'id': None,
                            'user_id': user.id,
                            'is_leader': bool(group and group.leader_id == user.id),
                            'joined_at': None,
                        })

            data = [
                _serialize_group(
                    group,
                    members_by_group.get(group.id, []) if include_members else None,
                )
                for group in page_groups
            ]
            return {'success': True, 'data': data, 'meta': meta}

        return _cached_json_response(request, 'groups', build_payload)

    if request.method == 'POST':
        denied = _ensure_owner_system_access(request)
        if denied:
            return denied
        try:
            body = json.loads(request.body)
            group = UserGroup.objects.create(
                group_name=body.get('group_name', ''),
                group_code=body.get('group_code') or None,
                description=body.get('description', ''),
                max_members=body.get('max_members', 50),
                created_by=_get_product_owner_id(request),
            )
            _invalidate_api_cache(request, 'groups')
            return JsonResponse({
                'success': True,
                'message': 'Group created successfully',
                'data': {'id': group.id, 'group_name': group.group_name},
            }, status=201)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.exception("api_groups POST error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_group_detail(request, group_id, *args, **kwargs):
    """GET / PUT / DELETE a single group."""
    try:
        group = _scope_groups_queryset(request).get(id=group_id)
    except ObjectDoesNotExist:
        return JsonResponse({'success': False, 'error': 'Group not found'}, status=404)

    if request.method == 'GET':
        members_qs = _scope_group_members_queryset(request).filter(group_id=group_id, status='active')
        member_list = [{
            'id': m.id,
            'user_id': m.user_id,
            'is_leader': m.is_leader,
            'joined_at': m.joined_at.isoformat() if m.joined_at else None,
        } for m in members_qs]

        if not member_list:
            user_members = _scope_users_queryset(request).filter(group_name=group.group_name, is_active=True)
            member_list = [{
                'id': None,
                'user_id': u.id,
                'is_leader': (group.leader_id == u.id),
                'joined_at': None,
            } for u in user_members]

        return JsonResponse({
            'success': True,
            'data': _serialize_group(group, member_list),
        })

    denied = _ensure_owner_system_access(request)
    if denied:
        return denied

    if request.method == 'PUT':
        try:
            body = json.loads(request.body)
            old_group_name = group.group_name
            new_group_name = body.get('group_name', group.group_name)
            group.group_name = new_group_name
            group.description = body.get('description', group.description)
            if 'max_members' in body:
                group.max_members = body['max_members']
            if 'current_members' in body:
                group.current_members = body['current_members']
            if 'is_flagged' in body:
                group.is_flagged = bool(body['is_flagged'])
            if 'status' in body:
                group.is_active = body['status'] == 'active'
            if 'leader_id' in body:
                group.leader_id = body['leader_id']
            group.save()

            if old_group_name != new_group_name:
                _scope_users_queryset(request).filter(group_name=old_group_name, is_active=True).update(group_name=new_group_name)

            _invalidate_api_cache(request, 'groups', 'users')
            return JsonResponse({
                'success': True,
                'message': 'Group updated successfully',
                'data': {'id': group.id, 'group_name': group.group_name},
            })
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.exception("api_group_detail PUT error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    if request.method == 'DELETE':
        _scope_users_queryset(request).filter(group_name=group.group_name).update(group_name='')
        _scope_group_members_queryset(request).filter(group_id=group_id).delete()
        group.delete()
        _invalidate_api_cache(request, 'groups', 'users')
        return JsonResponse({'success': True, 'message': 'Group deleted successfully'})

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def _refresh_group_member_count(request, group_id):
    group = _scope_groups_queryset(request).get(id=group_id)
    group.current_members = _scope_group_members_queryset(request).filter(group_id=group_id, status='active').count()
    group.save(update_fields=['current_members', 'updated_at'])
    return group


def api_group_move_member(request, *args, **kwargs):
    """Move one member to another group in a single owner-only transaction."""
    denied = _ensure_owner_system_access(request)
    if denied:
        return denied

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    user_id = body.get('user_id')
    target_group_id = body.get('target_group_id') or body.get('group_id')

    if not user_id or not target_group_id:
        return JsonResponse({'success': False, 'error': 'user_id and target_group_id are required'}, status=400)

    try:
        user_id = int(user_id)
        target_group_id = int(target_group_id)
    except (TypeError, ValueError):
        return JsonResponse({'success': False, 'error': 'Invalid user_id or target_group_id'}, status=400)

    try:
        with transaction.atomic():
            user = _scope_users_queryset(request).select_for_update().get(id=user_id)
            target_group = _scope_groups_queryset(request).select_for_update().get(id=target_group_id)

            target_members = _scope_group_members_queryset(request).filter(group_id=target_group_id, status='active')
            already_in_target = target_members.filter(user_id=user_id).exists()
            if not already_in_target and target_group.max_members and target_members.count() >= target_group.max_members:
                return JsonResponse({'success': False, 'error': 'Target group is full'}, status=400)

            old_group_ids = list(
                _scope_group_members_queryset(request)
                .filter(user_id=user_id, status='active')
                .values_list('group_id', flat=True)
            )

            _scope_groups_queryset(request).filter(leader_id=user_id).update(leader_id=None)
            _scope_group_members_queryset(request).filter(user_id=user_id).exclude(group_id=target_group_id).delete()

            member, _created = UserGroupMember.objects.get_or_create(
                user_id=user_id,
                group_id=target_group_id,
                defaults={'is_leader': False, 'status': 'active'},
            )
            if member.status != 'active' or member.is_leader:
                member.status = 'active'
                member.is_leader = False
                member.save(update_fields=['status', 'is_leader'])

            user.group_name = target_group.group_name
            user.save(update_fields=['group_name', 'updated_at'])

            for group_id in set(old_group_ids + [target_group_id]):
                _refresh_group_member_count(request, group_id)

        _invalidate_api_cache(request, 'groups', 'users')
        return JsonResponse({
            'success': True,
            'message': 'Member moved successfully',
            'data': {
                'user': _serialize_user(user),
                'group': _serialize_group(_scope_groups_queryset(request).get(id=target_group_id)),
            },
        })
    except ObjectDoesNotExist:
        return JsonResponse({'success': False, 'error': 'User or target group not found'}, status=404)
    except Exception as e:
        logger.exception("api_group_move_member error")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def api_groups_reformat(request, *args, **kwargs):
    """Rebuild this owner's groups from a requested size and fill members into them."""
    denied = _ensure_owner_system_access(request)
    if denied:
        return denied

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    try:
        group_size = int(body.get('group_size') or body.get('size') or 0)
    except (TypeError, ValueError):
        group_size = 0

    if group_size < 1 or group_size > 500:
        return JsonResponse({'success': False, 'error': 'Group size must be between 1 and 500'}, status=400)

    order_type = body.get('order_type') if body.get('order_type') in {'alphabetical', 'numerical'} else 'alphabetical'

    def group_name_for(index):
        if order_type == 'numerical':
            return f'Group {index + 1}'
        name = ''
        n = index + 1
        while n > 0:
            n, rem = divmod(n - 1, 26)
            name = chr(65 + rem) + name
        return name

    try:
        with transaction.atomic():
            users = list(_scope_users_queryset(request).filter(is_active=True).order_by('created_at', 'id'))
            _scope_group_members_queryset(request).delete()
            _scope_groups_queryset(request).delete()
            _scope_users_queryset(request).filter(is_active=True).update(group_name='')

            owner_id = _get_product_owner_id(request)
            group_count = (len(users) + group_size - 1) // group_size if users else 0
            created_groups = []
            for index in range(group_count):
                created_groups.append(UserGroup.objects.create(
                    group_name=group_name_for(index),
                    max_members=group_size,
                    current_members=0,
                    created_by=owner_id,
                ))

            for index, user in enumerate(users):
                group = created_groups[index // group_size]
                UserGroupMember.objects.create(
                    user_id=user.id,
                    group_id=group.id,
                    is_leader=False,
                    status='active',
                )
                user.group_name = group.group_name
                user.save(update_fields=['group_name', 'updated_at'])

            for group in created_groups:
                group.current_members = _scope_group_members_queryset(request).filter(group_id=group.id, status='active').count()
                group.save(update_fields=['current_members', 'updated_at'])

        _invalidate_api_cache(request, 'groups', 'users')
        return JsonResponse({
            'success': True,
            'message': 'Groups reformatted successfully',
            'data': {
                'group_size': group_size,
                'group_count': group_count,
                'member_count': len(users),
            },
        })
    except Exception as e:
        logger.exception("api_groups_reformat error")
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def api_group_members(request, *args, **kwargs):
    """GET: list members.  POST: add member (owner only)."""
    if request.method == 'GET':
        members = _scope_group_members_queryset(request)

        group_id = request.GET.get('group_id')
        user_id = request.GET.get('user_id')
        status = request.GET.get('status')

        if group_id is not None:
            try:
                members = members.filter(group_id=int(group_id))
            except ValueError:
                return JsonResponse({'success': False, 'error': 'Invalid group_id'}, status=400)

        if user_id is not None:
            try:
                members = members.filter(user_id=int(user_id))
            except ValueError:
                return JsonResponse({'success': False, 'error': 'Invalid user_id'}, status=400)

        members = members.filter(status=status) if status is not None else members.filter(status='active')

        page, meta = _paginate_queryset(request, members)
        data = [{
            'id': m.id,
            'user_id': m.user_id,
            'group_id': m.group_id,
            'is_leader': m.is_leader,
            'status': m.status,
            'joined_at': m.joined_at.isoformat() if m.joined_at else None,
        } for m in page]
        return JsonResponse({'success': True, 'data': data, 'meta': meta})

    if request.method == 'POST':
        denied = _ensure_owner_system_access(request)
        if denied:
            return denied
        try:
            body = json.loads(request.body)
            user_id = body.get('user_id')
            group_id = body.get('group_id')
            is_leader = body.get('is_leader', False)
            status = body.get('status', 'active')

            if not user_id or not group_id:
                return JsonResponse({'success': False, 'error': 'user_id and group_id are required'}, status=400)

            try:
                user = _scope_users_queryset(request).get(id=user_id)
            except ObjectDoesNotExist:
                return JsonResponse({'success': False, 'error': 'User not found'}, status=404)

            try:
                group = _scope_groups_queryset(request).get(id=group_id)
            except ObjectDoesNotExist:
                return JsonResponse({'success': False, 'error': 'Group not found'}, status=404)

            if _scope_group_members_queryset(request).filter(user_id=user_id, group_id=group_id).exists():
                return JsonResponse({'success': False, 'error': 'User is already a member of this group'}, status=400)

            member = UserGroupMember.objects.create(
                user_id=user_id,
                group_id=group_id,
                is_leader=is_leader,
                status=status,
            )
            user.group_name = group.group_name
            user.save()
            group.current_members = _scope_group_members_queryset(request).filter(group_id=group_id, status='active').count()
            group.save()
            _invalidate_api_cache(request, 'groups', 'users')

            return JsonResponse({
                'success': True,
                'message': 'Member added to group successfully',
                'data': {
                    'id': member.id,
                    'user_id': member.user_id,
                    'group_id': member.group_id,
                    'is_leader': member.is_leader,
                    'status': member.status,
                },
            }, status=201)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.exception("api_group_members POST error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_group_member_detail(request, member_id, *args, **kwargs):
    """GET / PUT / DELETE a single group member (owner only)."""
    denied = _ensure_owner_system_access(request)
    if denied:
        return denied
    try:
        member = _scope_group_members_queryset(request).get(id=member_id)
    except ObjectDoesNotExist:
        return JsonResponse({'success': False, 'error': 'Member not found'}, status=404)

    if request.method == 'GET':
        return JsonResponse({
            'success': True,
            'data': {
                'id': member.id,
                'user_id': member.user_id,
                'group_id': member.group_id,
                'is_leader': member.is_leader,
                'status': member.status,
                'joined_at': member.joined_at.isoformat() if member.joined_at else None,
            },
        })

    if request.method == 'PUT':
        try:
            body = json.loads(request.body)
            if 'is_leader' in body:
                member.is_leader = body['is_leader']
            if 'status' in body:
                member.status = body['status']
            member.save()

            group = _scope_groups_queryset(request).get(id=member.group_id)
            group.current_members = _scope_group_members_queryset(request).filter(group_id=member.group_id, status='active').count()
            group.save()
            _invalidate_api_cache(request, 'groups', 'users')

            return JsonResponse({
                'success': True,
                'message': 'Member updated successfully',
                'data': {
                    'id': member.id,
                    'user_id': member.user_id,
                    'group_id': member.group_id,
                    'is_leader': member.is_leader,
                    'status': member.status,
                },
            })
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.exception("api_group_member_detail PUT error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    if request.method == 'DELETE':
        try:
            group_id = member.group_id
            member.delete()
            group = _scope_groups_queryset(request).get(id=group_id)
            group.current_members = _scope_group_members_queryset(request).filter(group_id=group_id, status='active').count()
            group.save()
            _invalidate_api_cache(request, 'groups', 'users')
            return JsonResponse({'success': True, 'message': 'Member removed from group successfully'})
        except Exception as e:
            logger.exception("api_group_member_detail DELETE error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


# ---------------------------------------------------------------------------
# API — System Settings: signup control
# ---------------------------------------------------------------------------

def api_signup_setting(request, *args, **kwargs):
    """GET / PUT the signup_allowed system setting."""
    SIGNUP_SETTING_KEY = 'signup_allowed'

    if request.method == 'GET':
        try:
            def build_payload():
                try:
                    setting = _scope_system_settings_queryset(request).get(setting_key=SIGNUP_SETTING_KEY)
                    signup_allowed = setting.setting_value == 'true'
                except ObjectDoesNotExist:
                    setting = SystemSetting.objects.create(
                        setting_key=SIGNUP_SETTING_KEY,
                        setting_value='true',
                        setting_type='boolean',
                        description='Controls whether new members can sign up',
                        created_by=_get_product_owner_id(request),
                    )
                    signup_allowed = True
                return {
                    'success': True,
                    'data': {'signup_allowed': signup_allowed, 'setting_key': SIGNUP_SETTING_KEY},
                }

            return _cached_json_response(request, 'signup_setting', build_payload)
        except Exception as e:
            logger.exception("api_signup_setting GET error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    if request.method == 'PUT':
        denied = _ensure_owner_system_access(request)
        if denied:
            return denied
        try:
            body = json.loads(request.body)
            signup_allowed = body.get('signup_allowed', True)
            setting, created = SystemSetting.objects.get_or_create(
                setting_key=SIGNUP_SETTING_KEY,
                created_by=_get_product_owner_id(request),
                defaults={
                    'setting_value': 'true' if signup_allowed else 'false',
                    'setting_type': 'boolean',
                    'description': 'Controls whether new members can sign up',
                },
            )
            if not created:
                setting.setting_value = 'true' if signup_allowed else 'false'
                setting.save()
            _invalidate_api_cache(request, 'signup_setting', 'system_settings')
            return JsonResponse({
                'success': True,
                'message': 'Signup setting updated successfully',
                'data': {'signup_allowed': signup_allowed, 'setting_key': SIGNUP_SETTING_KEY},
            })
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.exception("api_signup_setting PUT error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


# ---------------------------------------------------------------------------
# API — System Settings: storage links
# ---------------------------------------------------------------------------

def api_system_settings(request, *args, **kwargs):
    """GET / PUT storage links and registration-mode settings."""
    STORAGE_NOTES_KEY = 'storage_notes_link'
    STORAGE_CALENDAR_KEY = 'storage_calendar_link'
    STORAGE_SUMMARY_KEY = 'storage_summary_link'
    REGISTRATION_MODE_KEY = 'registration_mode'
    REGISTRATION_LINK_KEY = 'registration_link'
    REGISTRATION_LINK_BEHAVIOR_KEY = 'registration_link_behavior'
    EXTERNAL_TABLE_MODE_KEY = 'external_table_mode'
    EXTERNAL_TABLE_NAME_KEY = 'external_table_name'
    EXTERNAL_TABLE_ID_KEY = 'external_table_id'
    EXTERNAL_SIGNUP_TARGET_ID_KEY = 'external_signup_target_id'
    EXTERNAL_SIGNUP_TARGET_NAME_KEY = 'external_signup_target_name'
    SLIDER_BACKGROUND_COLOR_KEY = 'slider_background_color'
    SLIDER_AUTO_ADVANCE_KEY = 'slider_auto_advance'
    allowed_keys = [
        STORAGE_NOTES_KEY, STORAGE_CALENDAR_KEY, STORAGE_SUMMARY_KEY,
        REGISTRATION_MODE_KEY, REGISTRATION_LINK_KEY, REGISTRATION_LINK_BEHAVIOR_KEY,
        EXTERNAL_TABLE_MODE_KEY, EXTERNAL_TABLE_NAME_KEY, EXTERNAL_TABLE_ID_KEY,
        EXTERNAL_SIGNUP_TARGET_ID_KEY, EXTERNAL_SIGNUP_TARGET_NAME_KEY,
        SLIDER_BACKGROUND_COLOR_KEY, SLIDER_AUTO_ADVANCE_KEY,
    ]

    def build_settings_payload():
        settings_map = {
            STORAGE_NOTES_KEY: '',
            STORAGE_CALENDAR_KEY: '',
            STORAGE_SUMMARY_KEY: '',
            REGISTRATION_MODE_KEY: 'default',
            REGISTRATION_LINK_KEY: _tenant_base_path(request) + '/groups/' if _tenant_base_path(request) else '/groups/',
            REGISTRATION_LINK_BEHAVIOR_KEY: 'redirect',
            EXTERNAL_TABLE_MODE_KEY: 'false',
            EXTERNAL_TABLE_NAME_KEY: '',
            EXTERNAL_TABLE_ID_KEY: '',
            EXTERNAL_SIGNUP_TARGET_ID_KEY: '',
            EXTERNAL_SIGNUP_TARGET_NAME_KEY: '',
            SLIDER_BACKGROUND_COLOR_KEY: '#0f1220',
            SLIDER_AUTO_ADVANCE_KEY: '6000',
        }
        for setting in _scope_system_settings_queryset(request).filter(setting_key__in=allowed_keys):
            settings_map[setting.setting_key] = setting.setting_value or ''
        return settings_map

    if request.method == 'GET':
        try:
            return _cached_json_response(
                request,
                'system_settings',
                lambda: {'success': True, 'data': build_settings_payload()},
            )
        except Exception as e:
            logger.exception("api_system_settings GET error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    if request.method == 'PUT':
        denied = _ensure_owner_system_access(request)
        if denied:
            return denied
        try:
            body = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON payload'}, status=400)

        owner_id = _get_product_owner_id(request)
        descriptions = {
            STORAGE_NOTES_KEY: 'External storage link for notes/past-files',
            STORAGE_CALENDAR_KEY: 'External storage link for calendar',
            STORAGE_SUMMARY_KEY: 'External storage link for UNI-COT.stf',
            REGISTRATION_MODE_KEY: 'Controls tenant registration mode (default, link, external_table)',
            REGISTRATION_LINK_KEY: 'External registration link for tenant sign-up',
            REGISTRATION_LINK_BEHAVIOR_KEY: 'Controls registration link behavior',
            EXTERNAL_TABLE_MODE_KEY: 'Controls whether external table signup mode is enabled',
            EXTERNAL_TABLE_NAME_KEY: 'Connected external signup table name',
            EXTERNAL_TABLE_ID_KEY: 'Connected external signup table id',
            EXTERNAL_SIGNUP_TARGET_ID_KEY: 'Preferred external signup target id',
            EXTERNAL_SIGNUP_TARGET_NAME_KEY: 'Preferred external signup target name',
            SLIDER_BACKGROUND_COLOR_KEY: 'Image slider background color',
            SLIDER_AUTO_ADVANCE_KEY: 'Image slider auto-advance interval in milliseconds',
        }

        for key in allowed_keys:
            if key not in body:
                continue
            value = str(body.get(key) or '').strip()
            setting, created = SystemSetting.objects.get_or_create(
                setting_key=key,
                created_by=owner_id,
                defaults={
                    'setting_value': value,
                    'setting_type': 'string',
                    'description': descriptions.get(key, ''),
                    'updated_by': owner_id,
                },
            )
            if not created:
                setting.setting_value = value
                setting.setting_type = 'string'
                setting.description = descriptions.get(key, setting.description)
                setting.updated_by = owner_id
                setting.save()

        _invalidate_api_cache(request, 'system_settings')
        return JsonResponse({
            'success': True,
            'message': 'Storage links updated successfully',
            'data': build_settings_payload(),
        })

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


# ---------------------------------------------------------------------------
# API — Registration Form Fields
# ---------------------------------------------------------------------------

def api_registration_fields(request, *args, **kwargs):
    """GET: list active fields.  POST: create field (owner only)."""
    if request.method == 'GET':
        def build_payload():
            owner_id = _get_product_owner_id(request)
            fields = RegistrationFormField.objects.filter(is_active=True)
            if owner_id is not None:
                fields = fields.filter(created_by=owner_id)
            fields = fields.order_by('display_order', 'id')
            data = [{
                'id': field.id,
                'name': field.field_name,
                'key': field.field_key or field.field_name,
                'type': field.field_type,
                'label': field.field_label,
                'placeholder': field.placeholder or '',
                'options': field.options or '',
                'required': 'yes' if field.is_required else 'no',
                'order': field.display_order,
            } for field in fields]
            return {'success': True, 'data': data}

        return _cached_json_response(request, 'registration_fields', build_payload)

    if request.method == 'POST':
        denied = _ensure_owner_system_access(request)
        if denied:
            return denied
        try:
            body = json.loads(request.body)
            field = RegistrationFormField.objects.create(
                field_name=body.get('name', ''),
                field_key=body.get('key', body.get('name', '').lower().replace(' ', '_')),
                field_type=body.get('type', 'text'),
                field_label=body.get('label', body.get('name', '')),
                placeholder=body.get('placeholder', ''),
                options=body.get('options', ''),
                is_required=body.get('required', 'no') == 'yes',
                display_order=body.get('order', 0),
                created_by=_get_product_owner_id(request),
            )
            _invalidate_api_cache(request, 'registration_fields')
            return JsonResponse({
                'success': True,
                'message': 'Field created successfully',
                'data': {
                    'id': field.id,
                    'name': field.field_name,
                    'key': field.field_key,
                    'type': field.field_type,
                    'label': field.field_label,
                    'required': 'yes' if field.is_required else 'no',
                },
            }, status=201)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.exception("api_registration_fields POST error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_registration_field_detail(request, field_id, *args, **kwargs):
    """GET / PUT / DELETE a single registration field (owner only)."""
    denied = _ensure_owner_system_access(request)
    if denied:
        return denied
    try:
        owner_id = _get_product_owner_id(request)
        field_qs = RegistrationFormField.objects.all()
        if owner_id is not None:
            field_qs = field_qs.filter(created_by=owner_id)
        field = field_qs.get(id=field_id)
    except ObjectDoesNotExist:
        return JsonResponse({'success': False, 'error': 'Field not found'}, status=404)

    if request.method == 'GET':
        return JsonResponse({
            'success': True,
            'data': {
                'id': field.id,
                'name': field.field_name,
                'key': field.field_key,
                'type': field.field_type,
                'label': field.field_label,
                'placeholder': field.placeholder or '',
                'options': field.options or '',
                'required': 'yes' if field.is_required else 'no',
                'order': field.display_order,
                'is_active': field.is_active,
            },
        })

    if request.method == 'PUT':
        try:
            body = json.loads(request.body)
            field.field_name = body.get('name', field.field_name)
            field.field_key = body.get('key', field.field_key)
            field.field_type = body.get('type', field.field_type)
            field.field_label = body.get('label', field.field_label)
            field.placeholder = body.get('placeholder', field.placeholder)
            field.options = body.get('options', field.options)
            field.is_required = body.get('required', 'no') == 'yes'
            field.display_order = body.get('order', field.display_order)
            field.save()
            _invalidate_api_cache(request, 'registration_fields')
            return JsonResponse({
                'success': True,
                'message': 'Field updated successfully',
                'data': {
                    'id': field.id,
                    'name': field.field_name,
                    'key': field.field_key,
                    'type': field.field_type,
                    'label': field.field_label,
                },
            })
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.exception("api_registration_field_detail PUT error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    if request.method == 'DELETE':
        field.delete()
        _invalidate_api_cache(request, 'registration_fields')
        return JsonResponse({'success': True, 'message': 'Field deleted successfully'})

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


# ---------------------------------------------------------------------------
# API — Bulk user clear
# ---------------------------------------------------------------------------

def api_users_clear_all(request, *args, **kwargs):
    """POST: delete all scoped users (owner only)."""
    denied = _ensure_owner_system_access(request)
    if denied:
        return denied

    if request.method == 'POST':
        try:
            users = _scope_users_queryset(request)
            count = users.count()
            users.delete()
            _invalidate_api_cache(request, 'users', 'groups')
            return JsonResponse({'success': True, 'message': f'{count} users deleted successfully'})
        except Exception as e:
            logger.exception("api_users_clear_all POST error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


# ---------------------------------------------------------------------------
# API — External Tables
# ---------------------------------------------------------------------------

def api_external_tables(request, *args, **kwargs):
    """GET: list tables.  POST: create table (owner only)."""
    if request.method == 'GET':
        def build_payload():
            tables = _scope_external_tables_queryset(request)
            page, meta = _paginate_queryset(request, tables)
            page_tables = list(page)
            data = [_serialize_external_table(table) for table in page_tables]

            include_records = request.GET.get('include_records') in {'1', 'true', 'yes'}
            if include_records and page_tables:
                try:
                    records_page_size = max(1, min(int(request.GET.get('records_page_size', 1000)), 1000))
                except (ValueError, TypeError):
                    records_page_size = 1000

                schemas_by_table = {
                    table.id: table.get_fields_list() if hasattr(table, 'get_fields_list') else []
                    for table in page_tables
                }
                records_by_table = {table.id: [] for table in page_tables}
                table_ids = list(records_by_table.keys())

                records = (
                    ExternalTableRecord.objects
                    .filter(table_id__in=table_ids)
                    .only('id', 'table_id', 'data', 'created_at')
                    .order_by('table_id', '-created_at')
                )

                for record in records:
                    table_records = records_by_table.get(record.table_id)
                    if table_records is None or len(table_records) >= records_page_size:
                        continue
                    serialized = _serialize_external_record(record)
                    serialized['data'] = normalize_record(
                        schemas_by_table.get(record.table_id, []),
                        serialized.get('data', {}),
                    )
                    table_records.append(serialized)

                for table_payload in data:
                    table_payload['records'] = records_by_table.get(table_payload['id'], [])

            return {'success': True, 'data': data, 'meta': meta}

        return _cached_json_response(request, 'external_tables', build_payload)

    if request.method == 'POST':
        denied = _ensure_owner_system_access(request)
        if denied:
            return denied
        try:
            body = json.loads(request.body)
            table_name = body.get('table_name', '')
            fields = body.get('fields', [])
            hidden_columns = _parse_json_field(body.get('hidden_columns', []), [])

            if not re.match(r'^[a-zA-Z0-9_]+$', table_name):
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid table name. Only letters, numbers, and underscores allowed.',
                }, status=400)

            table = ExternalTable.objects.create(
                table_name=table_name,
                fields_schema=fields if isinstance(fields, list) else [],
                hidden_columns=hidden_columns if isinstance(hidden_columns, list) else [],
                created_by=_get_product_owner_id(request),
            )
            _invalidate_api_cache(request, 'external_tables')
            return JsonResponse({
                'success': True,
                'message': 'External table created successfully',
                'data': _serialize_external_table(table),
            }, status=201)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.exception("api_external_tables POST error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_external_table_detail(request, table_id, *args, **kwargs):
    """GET / PUT / PATCH / DELETE a single external table."""
    try:
        table = _scope_external_tables_queryset(request).get(id=table_id)
    except ObjectDoesNotExist:
        return JsonResponse({'success': False, 'error': 'Table not found'}, status=404)

    if request.method == 'GET':
        return JsonResponse({'success': True, 'data': _serialize_external_table(table)})

    denied = _ensure_owner_system_access(request)
    if denied:
        return denied

    if request.method in ('PUT', 'PATCH'):
        try:
            body = json.loads(request.body)

            if 'table_name' in body:
                new_name = str(body.get('table_name', '')).strip()
                if not new_name:
                    return JsonResponse({'success': False, 'error': 'Table name cannot be empty'}, status=400)
                if _scope_external_tables_queryset(request).exclude(id=table.id).filter(table_name__iexact=new_name).exists():
                    return JsonResponse({'success': False, 'error': 'Another table with this name already exists'}, status=400)
                table.table_name = new_name

            if 'fields_schema' in body:
                fields_schema = body.get('fields_schema', [])
                if isinstance(fields_schema, list):
                    table.fields_schema = fields_schema
                elif isinstance(fields_schema, str):
                    try:
                        table.fields_schema = json.loads(fields_schema)
                    except json.JSONDecodeError:
                        table.fields_schema = []
                else:
                    table.fields_schema = []

            if 'hidden_columns' in body:
                hidden_columns = _parse_json_field(body.get('hidden_columns', []), [])
                table.hidden_columns = hidden_columns if isinstance(hidden_columns, list) else []

            if 'is_visible' in body:
                table.is_visible = bool(body.get('is_visible'))
            if 'is_active' in body:
                table.is_active = bool(body.get('is_active'))

            table.save()
            _invalidate_api_cache(request, 'external_tables', f'external_table_records:{table.id}')
            return JsonResponse({
                'success': True,
                'message': 'External table updated successfully',
                'data': _serialize_external_table(table),
            })
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.exception("api_external_table_detail PUT error")
            return JsonResponse({'success': False, 'error': str(e)}, status=500)

    if request.method == 'DELETE':
        table_id_for_cache = table.id
        table.delete()
        _invalidate_api_cache(request, 'external_tables', f'external_table_records:{table_id_for_cache}')
        return JsonResponse({'success': True, 'message': 'External table deleted successfully'})

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_external_table_records(request, table_id, *args, **kwargs):
    """GET: list records.  POST: add record."""
    try:
        table = _scope_external_tables_queryset(request).get(id=table_id)
    except ObjectDoesNotExist:
        logger.warning("api_external_table_records: table %s not found", table_id)
        return JsonResponse({'success': False, 'error': 'Table not found'}, status=404)

    if request.method == 'GET':
        def build_payload():
            records = ExternalTableRecord.objects.filter(table=table)
            schema = table.get_fields_list() if hasattr(table, 'get_fields_list') else []
            page, meta = _paginate_queryset(request, records)
            normalized_records = []
            for record in page:
                serialized = _serialize_external_record(record)
                serialized['data'] = normalize_record(schema, serialized.get('data', {}))
                normalized_records.append(serialized)
            return {'success': True, 'data': normalized_records, 'meta': meta}

        return _cached_json_response(request, f'external_table_records:{table.id}', build_payload)

    if request.method == 'POST':
        denied = _ensure_owner_system_access(request)
        if denied:
            return denied
        try:
            body = json.loads(request.body)
            record_data = body.get('data', {})
            if not isinstance(record_data, dict):
                record_data = {}

            record = ExternalTableRecord.objects.create(table=table, data=record_data)
            _sync_external_table_record_count(table)
            _invalidate_api_cache(request, 'external_tables', f'external_table_records:{table.id}')
            return JsonResponse({
                'success': True,
                'message': 'Record added successfully',
                'data': _serialize_external_record(record),
            }, status=201)
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.exception("api_external_table_records POST error")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_external_table_record_detail(request, table_id, record_id, *args, **kwargs):
    """GET / PUT / PATCH / DELETE a single external table record (owner only)."""
    denied = _ensure_owner_system_access(request)
    if denied:
        return denied

    try:
        table = _scope_external_tables_queryset(request).get(id=table_id)
    except ObjectDoesNotExist:
        return JsonResponse({'success': False, 'error': 'Table not found'}, status=404)

    try:
        record = ExternalTableRecord.objects.get(id=record_id, table=table)
    except ObjectDoesNotExist:
        return JsonResponse({'success': False, 'error': 'Record not found'}, status=404)

    if request.method == 'GET':
        return JsonResponse({'success': True, 'data': _serialize_external_record(record)})

    if request.method in ('PUT', 'PATCH'):
        try:
            body = json.loads(request.body)
            current_data = _serialize_external_record(record)['record_data']
            incoming_data = body.get('data')
            if isinstance(incoming_data, dict):
                current_data.update(incoming_data)

            for key in ('status', 'is_approved', 'is_rejected', 'notes'):
                if key in body:
                    current_data[key] = body.get(key)

            # Assign the dict directly — JSONField handles serialisation.
            record.data = current_data
            record.save()
            _sync_external_table_record_count(table)
            _invalidate_api_cache(request, 'external_tables', f'external_table_records:{table.id}')
            return JsonResponse({
                'success': True,
                'message': 'Record updated successfully',
                'data': _serialize_external_record(record),
            })
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            logger.exception("api_external_table_record_detail PUT error")
            return JsonResponse({'success': False, 'error': str(e)}, status=500)

    if request.method == 'DELETE':
        record.delete()
        _sync_external_table_record_count(table)
        _invalidate_api_cache(request, 'external_tables', f'external_table_records:{table.id}')
        return JsonResponse({'success': True, 'message': 'Record deleted successfully'})

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


# ---------------------------------------------------------------------------
# API — Registration validation & external-table signup
# ---------------------------------------------------------------------------

def api_validate_registration(request, *args, **kwargs):
    """POST: validate a registration number against the User table."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    if _rate_limit(request, 'validate_registration', limit=20, window_seconds=300):
        return JsonResponse({'success': False, 'error': 'Too many validation attempts. Please wait and try again.'}, status=429)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    registration_number = body.get('registration_number', '').strip()
    if not registration_number:
        return JsonResponse({'success': False, 'error': 'Registration number is required'}, status=400)

    try:
        user = _scope_users_queryset(request).get(registration_number=registration_number)
        return JsonResponse({
            'success': True,
            'valid': True,
            'user': {
                'id': user.id,
                'full_name': user.full_name,
                'email': user.email,
                'registration_number': user.registration_number,
            },
        })
    except ObjectDoesNotExist:
        return JsonResponse({'success': True, 'valid': False, 'error': 'Registration number not found'})
    except Exception as e:
        logger.exception("api_validate_registration error")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def api_external_table_signup(request, *args, **kwargs):
    """POST: submit a member signup record for an external table."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    if _rate_limit(request, 'external_table_signup', limit=10, window_seconds=300):
        return JsonResponse({'success': False, 'error': 'Too many signup attempts. Please wait and try again.'}, status=429)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    table_id = body.get('table_id')
    registration_number = body.get('registration_number', '').strip()
    name = body.get('name', '').strip()
    email = body.get('email', '').strip()
    phone = body.get('phone', '').strip()
    notes = body.get('notes', '').strip()

    if not all([table_id, registration_number, name, email]):
        return JsonResponse({'success': False, 'error': 'All required fields must be provided'}, status=400)

    try:
        table = _scope_external_tables_queryset(request).get(id=table_id)
    except ObjectDoesNotExist:
        return JsonResponse({'success': False, 'error': 'Table not found'}, status=404)

    try:
        user = _scope_users_queryset(request).get(registration_number=registration_number)
    except ObjectDoesNotExist:
        return JsonResponse({'success': False, 'error': 'Invalid registration number'}, status=400)

    # Use the proper JSONField lookup — avoids false matches from substring search.
    if ExternalTableRecord.objects.filter(table=table, data__registration_number=registration_number).exists():
        return JsonResponse({'success': False, 'error': 'You have already applied for this table'}, status=400)

    record_data = {
        'full_name': name,
        'registration_number': registration_number,
        'email': email,
        'phone': phone,
        'notes': notes,
        'user_id': user.id,
        'status': 'pending',
        'submitted_at': datetime.now().isoformat(),
    }

    try:
        record = ExternalTableRecord.objects.create(table=table, data=record_data)
        _sync_external_table_record_count(table)
        _invalidate_api_cache(request, 'external_tables', f'external_table_records:{table.id}')
        return JsonResponse({
            'success': True,
            'message': 'Application submitted successfully',
            'data': {'id': record.id, 'status': 'pending'},
        }, status=201)
    except Exception as e:
        logger.exception("api_external_table_signup error")
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def api_external_table_toggle_visibility(request, table_id):
    """POST: toggle is_visible on an external table (owner only)."""
    try:
        table = _scope_external_tables_queryset(request).get(id=table_id)
    except ObjectDoesNotExist:
        return JsonResponse({'success': False, 'error': 'Table not found'}, status=404)

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    denied = _ensure_owner_system_access(request)
    if denied:
        return denied

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    is_visible = body.get('is_visible')
    if is_visible is None:
        return JsonResponse({'success': False, 'error': 'is_visible field is required'}, status=400)

    if isinstance(is_visible, str):
        table.is_visible = is_visible.lower() in ('true', '1', 'yes', 'on')
    else:
        table.is_visible = bool(is_visible)
    table.save()
    _invalidate_api_cache(request, 'external_tables', f'external_table_records:{table.id}')

    return JsonResponse({
        'success': True,
        'message': f'Table visibility {"enabled" if table.is_visible else "disabled"}',
        'data': {'id': table.id, 'is_visible': table.is_visible},
    })
