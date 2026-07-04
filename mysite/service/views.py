"""
Service views updated for multi-tenant SaaS platform.
Includes tenant-aware views and API endpoints.
"""

import os
import json
import logging
import uuid
from functools import wraps
from time import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.db.models import Avg, Count
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django_tenants.utils import schema_context
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_http_methods

from .models import OwnerUser, Member, Comment
from core.models import SystemSetting, User as CoreUser
from customers.models import CRTenant, TenantSubscription, create_owner_tenant, get_base_domain


logger = logging.getLogger(__name__)
WELCOME_PAGE_COMMENT_LIMIT = 5
OWNER_VAULT_NOTE_KEY = 'owner_file_manager_vault_note'


def _json_error(message, status=400):
    return JsonResponse({'error': message}, status=status)


def _owner_vault_payload(setting=None):
    return {
        'has_saved_note': bool(setting and (setting.setting_value or '').strip()),
        'updated_at': setting.updated_at.isoformat() if setting and setting.updated_at else None,
    }


def _build_access_domain(domain):
    if not domain:
        return None
    if settings.DEBUG and domain.endswith('.localhost') and ':' not in domain:
        return f'{domain}:8000'
    return domain


def _tenant_route_kwargs(request=None, tenant=None):
    tenant = tenant or getattr(request, 'tenant', None)

    if not tenant and request is not None:
        session_tenant_id = request.session.get('tenant_id')
        if session_tenant_id:
            tenant = getattr(request, 'tenant', None)

    if tenant and getattr(tenant, 'id', None) and getattr(tenant, 'subdomain', None) and getattr(tenant, 'tenant_key', None):
        return {
            'tenant_slug': tenant.subdomain,
            'tenant_id': tenant.id,
            'tenant_key': tenant.tenant_key,
        }
    return {}


def _tenant_url(view_name, request=None, tenant=None, **kwargs):
    route_kwargs = _tenant_route_kwargs(request=request, tenant=tenant)
    route_kwargs.update(kwargs)
    if route_kwargs:
        tenant_view_name = view_name.replace('service:', 'tenant_service:')
        return reverse(tenant_view_name, kwargs=route_kwargs)
    return reverse(view_name)


def _tenant_redirect(request, view_name, tenant=None, **kwargs):
    return redirect(_tenant_url(view_name, request=request, tenant=tenant, **kwargs))


def _tenant_base_path(request=None, tenant=None):
    route_kwargs = _tenant_route_kwargs(request=request, tenant=tenant)
    tenant = tenant or getattr(request, 'tenant', None)
    if tenant and getattr(tenant, 'schema_name', None) == 'public':
        return "/service"
    if route_kwargs:
        return f"/t/{route_kwargs['tenant_slug']}/{route_kwargs['tenant_id']}/{route_kwargs['tenant_key']}"
    return "/service"


def _get_owner_core_user(owner, reg_number):
    if not owner or not reg_number:
        return None
    return CoreUser.objects.filter(
        created_by=owner.id,
        registration_number=reg_number,
        is_active=True,
    ).first()


def _parse_json_body(request):
    try:
        return json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return None


def _rate_limit(request, key, limit=5, window_seconds=60):
    """
    Simple session-based rate limit (per browser/session).
    Returns True if the limit is exceeded.
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


def _build_owner_admin_session_payload(owner, tenant):
    return {
        'authenticated': True,
        'owner': {
            'id': owner.id,
            'email': owner.email,
            'program_name': owner.program_name,
        },
        'tenant': {
            'id': tenant.id,
            'name': tenant.name,
            'subdomain': tenant.subdomain,
            'tenant_key': tenant.tenant_key,
            'path_base': _tenant_base_path(tenant=tenant),
        }
    }


def _build_review_summary(comment_queryset):
    total_reviews = comment_queryset.count()
    average_rating = comment_queryset.aggregate(avg=Avg('rating'))['avg'] or 0
    rating_counts = {rating: 0 for rating in range(1, 6)}

    for item in comment_queryset.values('rating').annotate(total=Count('id')):
        rating_counts[item['rating']] = item['total']

    rating_bars = []
    for rating in range(5, 0, -1):
        total_for_rating = rating_counts[rating]
        percentage = round((total_for_rating / total_reviews) * 100, 1) if total_reviews else 0
        rating_bars.append({
            'rating': rating,
            'count': total_for_rating,
            'percentage': percentage,
        })

    star_states = []
    for index in range(5):
        remaining = average_rating - index
        if remaining >= 1:
            star_states.append('full')
        elif remaining >= 0.5:
            star_states.append('half')
        else:
            star_states.append('empty')

    return {
        'total_reviews': total_reviews,
        'average_rating': round(average_rating, 1),
        'average_rating_display': f"{average_rating:.1f}",
        'rating_bars': rating_bars,
        'star_states': star_states,
    }


def _get_tenant_owner(tenant):
    if not tenant:
        return None

    owner_id = getattr(tenant, 'owner_id', None)
    if not owner_id:
        return None

    with schema_context(getattr(settings, 'PUBLIC_SCHEMA_NAME', 'public')):
        return OwnerUser.objects.filter(id=owner_id, is_active=True).first()


def _get_owner_by_id(owner_id, include_inactive=False):
    if not owner_id:
        return None

    with schema_context(getattr(settings, 'PUBLIC_SCHEMA_NAME', 'public')):
        queryset = OwnerUser.objects.select_related('tenant').filter(id=owner_id)
        if not include_inactive:
            queryset = queryset.filter(is_active=True)
        return queryset.first()


def _get_owner_by_email(email, include_inactive=False):
    if not email:
        return None

    with schema_context(getattr(settings, 'PUBLIC_SCHEMA_NAME', 'public')):
        queryset = OwnerUser.objects.filter(email__iexact=email)
        if not include_inactive:
            queryset = queryset.filter(is_active=True)
        return queryset.first()


def _get_member_by_id(member_id):
    if not member_id:
        return None
    return Member.objects.filter(id=member_id).first()


def _find_member_login_record(reg_number, password, tenant=None):
    reg_number = (reg_number or '').strip()
    if not reg_number or not password:
        return None

    candidate_tenants = []
    if tenant and getattr(tenant, 'schema_name', None) not in {None, '', 'public'}:
        candidate_tenants.append(tenant)
    else:
        with schema_context(getattr(settings, 'PUBLIC_SCHEMA_NAME', 'public')):
            candidate_tenants = list(CRTenant.objects.filter(is_active=True).order_by('id'))

    for candidate_tenant in candidate_tenants:
        schema_name = getattr(candidate_tenant, 'schema_name', None)
        if not schema_name or schema_name == 'public':
            continue

        with schema_context(schema_name):
            member = Member.objects.filter(reg_number=reg_number, is_active=True).first()
            if not member or not check_password(password, member.password):
                continue

            owner = _get_owner_by_id(member.owner_id, include_inactive=True)
            if not owner:
                continue

            if not _get_owner_core_user(owner, reg_number):
                continue

            return {
                'member_id': member.id,
                'reg_number': member.reg_number,
                'program_name': member.program_name,
                'owner_id': owner.id,
                'tenant': candidate_tenant,
            }

    return None


def _has_owner_admin_access(request, tenant):
    if not tenant or getattr(tenant, 'schema_name', None) in {None, '', 'public'}:
        return False

    user = request.session.get('service_user') or {}
    if user.get('user_type') != 'owner':
        return False

    owner = _get_owner_by_id(user.get('owner_id'))
    if not owner:
        return False

    return getattr(tenant, 'owner_id', None) == owner.id and getattr(tenant, 'is_active', False)


def service_login_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get('service_user'):
            messages.error(request, 'Please log in to continue.')
            return _tenant_redirect(request, 'service:welcome')
        return view_func(request, *args, **kwargs)
    return wrapper


def service_role_required(required_role):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            user = request.session.get('service_user')
            if not user or user.get('user_type') != required_role:
                messages.error(request, 'Access denied.')
                return _tenant_redirect(request, 'service:welcome')
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def _clear_service_session(request):
    for key in (
        'service_user',
        'tenant_id',
        'program_name',
        'tenant_subdomain',
        'tenant_key',
    ):
        request.session.pop(key, None)
    request.session.modified = True


def _get_tenant_from_request(request):
    """Get tenant from request or session"""
    # Check request.tenant (set by middleware)
    tenant = getattr(request, 'tenant', None)
    
    if not tenant and request.session.get('tenant_id'):
        try:
            tenant_id = int(request.session.get('tenant_id'))
            from customers.models import CRTenant

            tenant = CRTenant.objects.filter(id=tenant_id, is_active=True).first()
        except (TypeError, ValueError):
            tenant = None

    if not tenant and request.session.get('service_user'):
        user = request.session.get('service_user')
        if user.get('user_type') == 'owner':
            owner = _get_owner_by_id(user.get('owner_id'), include_inactive=True)
            if owner:
                tenant = getattr(owner, 'tenant', None)
        elif user.get('user_type') == 'member':
            member = _get_member_by_id(user.get('member_id'))
            if member:
                owner = _get_owner_by_id(member.owner_id, include_inactive=True)
                if owner:
                    tenant = getattr(owner, 'tenant', None)
    
    return tenant


def welcome(request, *args, **kwargs):
    """Welcome page - dynamic based on user type and tenant"""
    user = request.session.get('service_user')
    tenant = _get_tenant_from_request(request)

    # Public demo host must stay isolated from owner/member data
    if not tenant:
        user = None
    
    # Get program name from tenant or session
    if tenant:
        program_name = tenant.name
    else:
        program_name = request.session.get('program_name', 'Service Portal')
    
    comments = []
    approved_comments = Comment.objects.none()
    member_count = 0
    comment_count = 0
    pending_comments = 0
    
    if user:
        user_type = user.get('user_type')
        owner_id = user.get('owner_id')
        
        if user_type == 'owner':
            owner = _get_owner_by_id(owner_id, include_inactive=True)
            if owner:
                if tenant:
                    program_name = tenant.name
                else:
                    program_name = owner.program_name
                    tenant = getattr(owner, 'tenant', None)

                member_count = Member.objects.filter(owner=owner).count()
                comment_count = Comment.objects.filter(owner=owner).count()
                pending_comments = Comment.objects.filter(owner=owner, status='pending').count()
                approved_comments = Comment.objects.filter(owner=owner, status='approved').prefetch_related('replies', 'member')
                comments = approved_comments[:WELCOME_PAGE_COMMENT_LIMIT]
        elif user_type == 'member':
            member = _get_member_by_id(user.get('member_id'))
            if member:
                program_name = member.program_name
                owner = _get_owner_by_id(member.owner_id, include_inactive=True)
                tenant = getattr(owner, 'tenant', None) if owner else tenant
                if owner:
                    approved_comments = Comment.objects.filter(owner=owner, status='approved').prefetch_related('replies', 'member')
                    comments = approved_comments[:WELCOME_PAGE_COMMENT_LIMIT]
    else:
        # Guest user - show approved comments
        if tenant:
            # Tenant-specific comments
            owner = _get_tenant_owner(tenant)
            if owner:
                approved_comments = Comment.objects.filter(owner=owner, status='approved').prefetch_related('replies', 'member')
                comments = approved_comments[:WELCOME_PAGE_COMMENT_LIMIT]
        else:
            # Public demo - do not leak owner data
            comments = []
            approved_comments = Comment.objects.none()

    review_summary = _build_review_summary(approved_comments)
    
    return render(request, 'index.html', {
        'program_name': program_name,
        'user': user,
        'comments': comments,
        'member_count': member_count,
        'comment_count': comment_count,
        'pending_comments': pending_comments,
        'review_summary': review_summary,
        'tenant': tenant,
        'tenant_base_path': _tenant_base_path(request=request, tenant=tenant),
    })


def service_welcome(request, *args, **kwargs):
    """
    Dedicated service welcome page with full tenant integration.
    This is the SaaS entry point for the welcome01.html integration.
    """
    user = request.session.get('service_user')
    tenant = _get_tenant_from_request(request)

    # Public demo host must stay isolated from owner/member data
    if not tenant:
        user = None
    
    context = {
        'user': user,
        'tenant': tenant,
    }
    
    if user and user.get('user_type') == 'owner':
        owner = _get_owner_by_id(user['owner_id'], include_inactive=True)
        if owner:
            tenant = getattr(owner, 'tenant', None)
            
            context.update({
                'owner': owner,
                'tenant': tenant,
                'program_name': tenant.name if tenant else owner.program_name,
                'domain_url': tenant.primary_domain_url if tenant else None,
                'paid_until': tenant.paid_until if tenant else None,
                'days_remaining': tenant.days_remaining if tenant else 0,
                'is_subscription_active': tenant.is_subscription_active if tenant else False,
                'member_count': Member.objects.filter(owner=owner).count(),
                'comment_count': Comment.objects.filter(owner=owner).count(),
            })
    elif user and user.get('user_type') == 'member':
        member = _get_member_by_id(user['member_id'])
        if member:
            context.update({
                'member': member,
                'program_name': member.program_name,
            })
    
    # Get comments based on tenant
    owner = _get_tenant_owner(tenant)
    if tenant and owner:
        approved_comments = Comment.objects.filter(
            owner=owner,
            status='approved'
        ).prefetch_related('replies', 'member')
        comments = approved_comments[:WELCOME_PAGE_COMMENT_LIMIT]
    else:
        approved_comments = Comment.objects.none()
        comments = []
    
    context['comments'] = comments
    context['review_summary'] = _build_review_summary(approved_comments)
    context['tenant_base_path'] = _tenant_base_path(request=request, tenant=tenant)
    
    return render(request, 'service/welcome.html', context)


@ensure_csrf_cookie
def system_demo(request, *args, **kwargs):
    """Serve the actual product shell; data isolation is handled by tenant-scoped APIs."""
    tenant = _get_tenant_from_request(request)
    tenant_requires_owner_admin_gate = bool(
        tenant and getattr(tenant, 'schema_name', None) not in {None, '', 'public'}
    )
    owner_admin_authenticated = _has_owner_admin_access(request, tenant) if tenant_requires_owner_admin_gate else False
    analytics = {
        'plan': 'basic',
        'package_label': 'Basic',
        'days_remaining': 0,
        'subscription_active': False,
        'payment_status': 'Unavailable',
        'progress_percent': 0,
        'is_trial': False,
        'alert_message': '',
        'whatsapp_group_url': (os.getenv('OWNER_WHATSAPP_GROUP_URL') or '').strip(),
    }

    if tenant and getattr(tenant, 'schema_name', None) != 'public':
        latest_subscription = tenant.subscriptions.filter(is_active=True).order_by('-created_at').first()
        plan = latest_subscription.plan if latest_subscription else ('trial' if tenant.is_trial else 'basic')
        package_label = plan.replace('_', ' ').title()
        days_remaining = tenant.days_remaining
        subscription_active = tenant.is_subscription_active
        is_trial = bool(tenant.is_trial or plan == TenantSubscription.STATUS_TRIAL)
        payment_status = 'Trial Active' if is_trial and subscription_active else ('Active' if subscription_active else 'Expired')

        progress_percent = 0
        if tenant.subscription_start and tenant.paid_until:
            total_days = max((tenant.paid_until - tenant.subscription_start).days, 1)
            elapsed_days = max((timezone.now().date() - tenant.subscription_start).days, 0)
            progress_percent = max(0, min(100, round((elapsed_days / total_days) * 100)))
        elif subscription_active:
            progress_percent = 100

        alert_message = ''
        if not subscription_active:
            alert_message = 'Your access time is out. Join the WhatsApp group or contact founder support to renew your package.'
        elif days_remaining <= 3:
            alert_message = f'Your package expires soon. Only {days_remaining} day{"s" if days_remaining != 1 else ""} remaining.'

        analytics.update({
            'plan': plan,
            'package_label': package_label,
            'days_remaining': days_remaining,
            'subscription_active': subscription_active,
            'payment_status': payment_status,
            'progress_percent': progress_percent,
            'is_trial': is_trial,
            'alert_message': alert_message,
        })

    return render(request, 'system_index.html', {
        'tenant': tenant,
        'tenant_base_path': _tenant_base_path(request=request, tenant=tenant),
        'owner_analytics': analytics,
        'tenant_requires_owner_admin_gate': tenant_requires_owner_admin_gate,
        'owner_admin_authenticated': owner_admin_authenticated,
    })


def login_view(request, *args, **kwargs):
    """Login view - handles both owner and member login"""
    if request.method == 'POST':
        if _rate_limit(request, 'login', limit=5, window_seconds=60):
            messages.error(request, 'Too many login attempts. Please wait a minute and try again.')
            return _tenant_redirect(request, 'service:welcome')
        
        login_identifier = request.POST.get('login_identifier', '').strip()
        password = request.POST.get('password', '')
        
        if '@' in login_identifier:
            # Owner login - allow from any domain/device
            owner = _get_owner_by_email(login_identifier)
            if owner and check_password(password, owner.password):
                # Clear session and start fresh
                request.session.flush()
                request.session.cycle_key()
                
                # Set owner session data
                request.session['service_user'] = {
                    'user_type': 'owner',
                    'owner_id': owner.id,
                    'email': owner.email,
                    'program_name': owner.program_name,
                }
                request.session['program_name'] = owner.program_name
                
                # Link owner's tenant to session (if they have one)
                owner_tenant = getattr(owner, 'tenant', None)
                if owner_tenant:
                    request.session['tenant_id'] = owner_tenant.id
                    request.session['tenant_subdomain'] = owner_tenant.subdomain
                    request.session['tenant_key'] = owner_tenant.tenant_key
                
                # Mark session as successfully authenticated
                request.session.modified = True
                request.session.set_expiry(60 * 60 * 24 * 7)  # 1 week
                
                messages.success(request, f'Welcome back, {owner.email}!')
                return _tenant_redirect(request, 'service:owner_dashboard', tenant=owner_tenant)
            
            # Founder login - allow Django admin/staff users into the founder dashboard
            founder = get_user_model().objects.filter(email__iexact=login_identifier, is_active=True).first()
            if founder and founder.is_staff and founder.check_password(password):
                request.session.flush()
                auth_login(request, founder)
                messages.success(request, 'Founder access granted.')
                return redirect('founder_saas_system_control')

            messages.error(request, 'Invalid email or password.')
        else:
            # Member login - allow from any device. If tenant context is present
            # ensure it matches the member's tenant; otherwise attach the member's
            # tenant to the session so they can access their dashboard from any
            # device.
            tenant = _get_tenant_from_request(request)
            reg_number = login_identifier
            member_record = _find_member_login_record(reg_number, password, tenant=tenant)

            if member_record:
                member_tenant = member_record['tenant']

                # Clear session and start fresh
                request.session.flush()
                request.session.cycle_key()

                # Set member session data
                request.session['service_user'] = {
                    'user_type': 'member',
                    'member_id': member_record['member_id'],
                    'reg_number': member_record['reg_number'],
                    'program_name': member_record['program_name'],
                    'owner_id': member_record['owner_id'],
                }
                request.session['program_name'] = member_record['program_name']

                # Attach member's tenant to session if available so dashboard
                # routing works regardless of the device used to sign up.
                if member_tenant:
                    request.session['tenant_id'] = member_tenant.id
                    request.session['tenant_subdomain'] = member_tenant.subdomain
                    request.session['tenant_key'] = member_tenant.tenant_key

                # Mark session as successfully authenticated
                request.session.modified = True
                request.session.set_expiry(60 * 60 * 24 * 7)  # 1 week

                messages.success(request, f'Welcome back, {member_record["reg_number"]}!')
                return _tenant_redirect(request, 'service:member_dashboard', tenant=member_tenant)
            else:
                messages.error(request, 'Invalid registration number or password.')
    
    return _tenant_redirect(request, 'service:welcome')


def register_view(request, *args, **kwargs):
    """Registration entry point.

    Public host: owner signup by email with tenant creation.
    Tenant host: member signup by registration number only.
    """
    if request.method == 'POST':
        if _rate_limit(request, 'register', limit=5, window_seconds=300):
            messages.error(request, 'Too many registration attempts. Please wait a few minutes and try again.')
            return _tenant_redirect(request, 'service:welcome')

        tenant = _get_tenant_from_request(request)
        if tenant and getattr(tenant, 'schema_name', None) != 'public':
            owner = _get_tenant_owner(tenant)
            if not owner:
                messages.error(request, 'This tenant is missing an owner account. Please contact support.')
                return _tenant_redirect(request, 'service:welcome', tenant=tenant)

            reg_number = request.POST.get('reg_number', '').strip()
            password = request.POST.get('password', '')
            confirm_password = request.POST.get('confirm_password', '')

            if not reg_number or not password:
                messages.error(request, 'Registration number and password are required.')
                return _tenant_redirect(request, 'service:welcome', tenant=tenant)

            if password != confirm_password:
                messages.error(request, 'Passwords do not match.')
                return _tenant_redirect(request, 'service:welcome', tenant=tenant)

            core_user = _get_owner_core_user(owner, reg_number)
            if not core_user:
                messages.error(
                    request,
                    'This registration number is not registered in this tenant. Please contact your admin first.',
                )
                return _tenant_redirect(request, 'service:welcome', tenant=tenant)

            existing_member = Member.objects.filter(reg_number=reg_number).first()
            if existing_member:
                existing_owner = _get_owner_by_id(existing_member.owner_id, include_inactive=True)
                existing_member_tenant = getattr(existing_owner, 'tenant', None) if existing_owner else None
                if existing_member_tenant and existing_member_tenant.id == tenant.id:
                    messages.error(request, 'This registration number already has a member account. Please log in.')
                else:
                    messages.error(request, 'This registration number already belongs to another tenant member account.')
                return _tenant_redirect(request, 'service:welcome', tenant=tenant)

            member = Member.objects.create(
                owner=owner,
                reg_number=reg_number,
                program_name=tenant.name or owner.program_name,
                password=make_password(password),
                is_active=True,
            )

            request.session.flush()
            request.session.cycle_key()
            request.session['service_user'] = {
                'user_type': 'member',
                'member_id': member.id,
                'reg_number': member.reg_number,
                'program_name': member.program_name,
                'owner_id': owner.id,
            }
            request.session['program_name'] = member.program_name
            request.session['tenant_id'] = tenant.id
            request.session['tenant_subdomain'] = tenant.subdomain
            request.session['tenant_key'] = tenant.tenant_key
            request.session.modified = True
            request.session.set_expiry(60 * 60 * 24 * 7)

            messages.success(request, f'Sign up successful. Welcome, {member.reg_number}!')
            return _tenant_redirect(request, 'service:member_dashboard', tenant=tenant)

        program_name = request.POST.get('program_name', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        confirm_password = request.POST.get('confirm_password', '')
        
        if not program_name or not email or not password:
            messages.error(request, 'All fields are required.')
            return _tenant_redirect(request, 'service:welcome')
        
        if password != confirm_password:
            messages.error(request, 'Passwords do not match.')
            return _tenant_redirect(request, 'service:welcome')
        
        if OwnerUser.objects.filter(email=email).exists():
            messages.error(request, 'Email already registered.')
            return _tenant_redirect(request, 'service:welcome')

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
        except Exception:
            error_ref = uuid.uuid4().hex[:8]
            logger.exception(
                "Owner signup failed during tenant creation [ref=%s email=%s program_name=%s]",
                error_ref,
                email,
                program_name,
            )
            messages.error(
                request,
                f'Registration could not be completed right now. Please try again in a moment. Ref: {error_ref}',
            )
            return _tenant_redirect(request, 'service:welcome')
        
        # Set session
        request.session.flush()
        request.session.cycle_key()
        request.session['service_user'] = {
            'user_type': 'owner',
            'owner_id': owner.id,
            'email': owner.email,
            'program_name': owner.program_name,
        }
        request.session['program_name'] = owner.program_name
        request.session['tenant_id'] = tenant.id
        request.session['tenant_subdomain'] = tenant.subdomain
        request.session['tenant_key'] = tenant.tenant_key
        
        messages.success(
            request,
            f'Registration successful! Your tenant link is ready.',
        )
        return _tenant_redirect(request, 'service:owner_dashboard', tenant=tenant)
    
    return _tenant_redirect(request, 'service:welcome')


def logout_view(request, *args, **kwargs):
    """Logout view"""
    request.session.flush()
    messages.success(request, 'You have been logged out.')
    return redirect(reverse('service:welcome'))


@service_login_required
@service_role_required('owner')
def owner_dashboard(request, *args, **kwargs):
    """Owner dashboard - full access, stats, moderation with tenant info"""
    user = request.session.get('service_user')
    
    owner = _get_owner_by_id(user.get('owner_id'), include_inactive=True)
    if not owner:
        _clear_service_session(request)
        messages.error(request, 'Your owner account could not be found. Please sign in again.')
        return _tenant_redirect(request, 'service:welcome')

    if not owner.is_active:
        _clear_service_session(request)
        messages.error(request, 'Your owner account is inactive. Please contact founder support.')
        return _tenant_redirect(request, 'service:welcome')
    
    # Get tenant info
    tenant = getattr(owner, 'tenant', None)
    if not tenant:
        messages.error(request, 'Your owner account is missing a tenant workspace. Please contact founder support.')
        return _tenant_redirect(request, 'service:welcome')

    if not tenant.is_active:
        _clear_service_session(request)
        messages.error(request, 'Your tenant workspace is disabled. Please contact founder support.')
        return _tenant_redirect(request, 'service:welcome')

    if not tenant.is_subscription_active:
        messages.warning(request, 'Your tenant subscription is inactive or expired. Please renew to continue.')
        return _tenant_redirect(request, 'service:subscription_expired', tenant=tenant)

    if request.method == 'POST':
        action = request.POST.get('action', '').strip()
        if action == 'save_contact_number':
            phone_number = request.POST.get('phone_number', '').strip()
            if not phone_number:
                messages.error(request, 'Contact number is required so founder can assign your custom domain.')
            else:
                owner.phone_number = phone_number
                owner.save(update_fields=['phone_number', 'updated_at'])
                messages.success(request, 'Contact number saved. Founder can now complete your custom domain setup.')
            return _tenant_redirect(request, 'service:owner_dashboard', tenant=tenant)
    
    members = Member.objects.filter(owner=owner)
    comments = Comment.objects.filter(owner=owner).order_by('-created_at')
    pending_comments = comments.filter(status='pending')
    approved_comments = comments.filter(status='approved')
    rejected_comments = comments.filter(status='rejected')
    
    # Subscription info
    days_remaining = 0
    subscription_active = False
    domain_url = None
    system_url = None
    
    if tenant:
        days_remaining = tenant.days_remaining
        subscription_active = tenant.is_subscription_active
        domain_url = _build_access_domain(tenant.primary_domain_url)
        system_url = request.build_absolute_uri(
            _tenant_url('service:system_demo', request=request, tenant=tenant)
        )
    
    return render(request, 'owner_dashboard.html', {
        'owner': owner,
        'members': members,
        'comments': comments,
        'pending_comments': pending_comments,
        'approved_comments': approved_comments,
        'rejected_comments': rejected_comments,
        'member_count': members.count(),
        'tenant': tenant,
        'domain_url': domain_url,
        'system_url': system_url,
        'tenant_base_path': _tenant_base_path(request=request, tenant=tenant),
        'days_remaining': days_remaining,
        'subscription_active': subscription_active,
        'needs_contact_number': not bool(owner.phone_number.strip()),
    })


@service_login_required
@service_role_required('member')
def member_dashboard(request, *args, **kwargs):
    """Member dashboard - view content, post comments"""
    user = request.session.get('service_user')
    
    try:
        member = _get_member_by_id(user.get('member_id'))
    except Exception:
        member = None
    if not member:
        _clear_service_session(request)
        messages.error(request, 'Your member account could not be found. Please sign in again.')
        return _tenant_redirect(request, 'service:welcome')

    if not member.is_active:
        _clear_service_session(request)
        messages.error(request, 'Your member account is inactive. Please contact your owner administrator.')
        return _tenant_redirect(request, 'service:welcome')

    owner = _get_owner_by_id(member.owner_id, include_inactive=True)
    if not owner:
        _clear_service_session(request)
        messages.error(request, 'Your owner account could not be found. Please sign in again.')
        return _tenant_redirect(request, 'service:welcome')

    if not owner.is_active:
        _clear_service_session(request)
        messages.error(request, 'Your owner account is inactive, so member access is currently unavailable.')
        return _tenant_redirect(request, 'service:welcome')

    tenant = getattr(owner, 'tenant', None)
    if not tenant:
        messages.error(request, 'Your member account is not linked to an active tenant workspace yet. Please contact your owner administrator.')
        return _tenant_redirect(request, 'service:welcome')

    if not tenant.is_active:
        _clear_service_session(request)
        messages.error(request, 'Your tenant workspace is disabled. Please contact your owner administrator.')
        return _tenant_redirect(request, 'service:welcome')

    if not tenant.is_subscription_active:
        messages.warning(request, 'Your tenant subscription is inactive or expired. Please contact your owner administrator.')
        return _tenant_redirect(request, 'service:subscription_expired', tenant=tenant)
    
    comments = Comment.objects.filter(owner=owner, status='approved')
    
    return render(request, 'member_dashboard.html', {
        'member': member,
        'comments': comments,
        'tenant_base_path': _tenant_base_path(request=request, tenant=tenant),
    })


@require_http_methods(["POST"])
def add_comment(request, *args, **kwargs):
    """Add comment - only for members"""
    user = request.session.get('service_user')
    if not user or user.get('user_type') != 'member':
        messages.error(request, 'Only members can post comments.')
        return _tenant_redirect(request, 'service:welcome')
    
    content = request.POST.get('content', '').strip()
    rating = request.POST.get('rating', 5)
    
    if not content:
        messages.error(request, 'Comment cannot be empty.')
        return _tenant_redirect(request, 'service:welcome')
    
    try:
        member = _get_member_by_id(user.get('member_id'))
        rating_value = min(max(int(rating), 1), 5)
    except (TypeError, ValueError):
        member = None
    if not member:
        messages.error(request, 'Invalid comment submission.')
        return _tenant_redirect(request, 'service:welcome')
    
    Comment.objects.create(
        member=member,
        owner_id=member.owner_id,
        content=content,
        rating=rating_value,
        status='pending',
    )
    
    messages.success(request, 'Comment submitted for moderation.')
    return _tenant_redirect(request, 'service:welcome')


def _get_scoped_comment(request, comment_id):
    tenant = _get_tenant_from_request(request)
    comment = Comment.objects.select_related('owner', 'member').prefetch_related('replies__member').filter(
        id=comment_id,
        status='approved',
    ).first()
    if not comment:
        return None
    if tenant and getattr(tenant, 'owner_id', None) and tenant.owner_id != comment.owner_id:
        return None
    return comment


@require_http_methods(["POST"])
def react_comment(request, comment_id, reaction, *args, **kwargs):
    """Persist likes/dislikes so counts stay consistent across devices."""
    user = request.session.get('service_user')
    if not user:
        return _json_error('Please log in to react to reviews.', status=401)

    if reaction not in {'like', 'dislike'}:
        return _json_error('Invalid reaction.')

    comment = _get_scoped_comment(request, comment_id)
    if not comment:
        return _json_error('Review not found.', status=404)

    reaction_state = request.session.get('comment_reactions', {})
    comment_key = str(comment.id)
    previous_reaction = reaction_state.get(comment_key)

    if previous_reaction == reaction:
        return JsonResponse({
            'success': True,
            'likes': comment.likes,
            'dislikes': comment.dislikes,
            'reaction': previous_reaction,
        })

    if previous_reaction == 'like' and comment.likes > 0:
        comment.likes -= 1
    elif previous_reaction == 'dislike' and comment.dislikes > 0:
        comment.dislikes -= 1

    if reaction == 'like':
        comment.likes += 1
    else:
        comment.dislikes += 1

    comment.save(update_fields=['likes', 'dislikes', 'updated_at'])
    reaction_state[comment_key] = reaction
    request.session['comment_reactions'] = reaction_state

    return JsonResponse({
        'success': True,
        'likes': comment.likes,
        'dislikes': comment.dislikes,
        'reaction': reaction,
    })


@require_http_methods(["POST"])
def reply_comment(request, comment_id, *args, **kwargs):
    """Persist member replies so they are visible to everyone on the tenant."""
    user = request.session.get('service_user')
    if not user or user.get('user_type') != 'member':
        return _json_error('Only logged-in members can reply.', status=401)

    member = Member.objects.filter(id=user.get('member_id'), is_active=True).first()
    if not member:
        return _json_error('Member account not found.', status=404)

    comment = _get_scoped_comment(request, comment_id)
    if not comment:
        return _json_error('Review not found.', status=404)

    if comment.owner_id != member.owner_id:
        return _json_error('This review belongs to a different tenant.', status=403)

    content = request.POST.get('content', '').strip()
    if not content:
        return _json_error('Reply content is required.')

    reply = comment.replies.create(member=member, content=content)
    return JsonResponse({
        'success': True,
        'reply': {
            'id': reply.id,
            'member_name': member.reg_number,
            'content': reply.content,
            'created_at': reply.created_at.strftime('%b %d, %Y'),
        }
    })


@require_http_methods(["POST"])
def register_member(request, *args, **kwargs):
    """Register a new member - only owners can do this"""
    user = request.session.get('service_user')
    if not user or user.get('user_type') != 'owner':
        messages.error(request, 'Only owners can register members.')
        return _tenant_redirect(request, 'service:welcome')
    
    owner = _get_owner_by_id(user.get('owner_id'), include_inactive=True)
    if not owner:
        return _tenant_redirect(request, 'service:welcome')
    
    reg_number = request.POST.get('reg_number', '').strip()
    program_name = request.POST.get('program_name', '').strip()
    password = request.POST.get('password', '')
    confirm_password = request.POST.get('confirm_password', '')
    
    if not reg_number or not program_name or not password:
        messages.error(request, 'All fields are required.')
        return _tenant_redirect(request, 'service:owner_dashboard')
    
    if password != confirm_password:
        messages.error(request, 'Passwords do not match.')
        return _tenant_redirect(request, 'service:owner_dashboard')
    
    if Member.objects.filter(reg_number=reg_number).exists():
        messages.error(request, 'Registration number already exists.')
        return _tenant_redirect(request, 'service:owner_dashboard')

    core_user = _get_owner_core_user(owner, reg_number)
    if not core_user:
        messages.error(request, 'This registration number must already exist in your main system records.')
        return _tenant_redirect(request, 'service:owner_dashboard')

    Member.objects.create(
        owner=owner,
        reg_number=reg_number,
        program_name=program_name,
        password=make_password(password),
        is_active=True,
    )
    
    messages.success(request, f'Member {reg_number} registered successfully.')
    return _tenant_redirect(request, 'service:owner_dashboard')


@require_http_methods(["POST"])
def moderate_comment(request, comment_id, action, *args, **kwargs):
    """Moderate comment - approve/reject (owner only)"""
    user = request.session.get('service_user')
    if not user or user.get('user_type') != 'owner':
        messages.error(request, 'Access denied.')
        return _tenant_redirect(request, 'service:welcome')
    
    comment = Comment.objects.filter(id=comment_id).first()
    if not comment:
        messages.error(request, 'Comment not found.')
        return _tenant_redirect(request, 'service:owner_dashboard')
    
    # Verify comment belongs to owner's tenant
    owner = _get_owner_by_id(user.get('owner_id'), include_inactive=True)
    if not owner:
        messages.error(request, 'Your owner account could not be found. Please sign in again.')
        return _tenant_redirect(request, 'service:welcome')
    if comment.owner != owner:
        messages.error(request, 'Access denied.')
        return _tenant_redirect(request, 'service:owner_dashboard')
    
    if action == 'approve':
        comment.status = 'approved'
        comment.save()
        messages.success(request, 'Comment approved.')
    elif action == 'reject':
        comment.status = 'rejected'
        comment.save()
        messages.success(request, 'Comment rejected.')
    elif action == 'delete':
        comment.delete()
        messages.success(request, 'Comment deleted.')
    
    return _tenant_redirect(request, 'service:owner_dashboard')


# API Endpoints for multi-tenancy

@require_http_methods(["POST"])
def api_create_tenant(request, *args, **kwargs):
    """
    API endpoint to create a new tenant (Owner signup via API)
    """
    data = _parse_json_body(request)
    if data is None:
        return _json_error('Invalid JSON')
    
    program_name = data.get('program_name', '').strip()
    email = data.get('email', '').strip()
    password = data.get('password', '')
    
    if not all([program_name, email, password]):
        return _json_error('All fields required')
    
    if OwnerUser.objects.filter(email=email).exists():
        return _json_error('Email already exists')

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
    except Exception:
        error_ref = uuid.uuid4().hex[:8]
        logger.exception(
            "API tenant creation failed [ref=%s email=%s program_name=%s]",
            error_ref,
            email,
            program_name,
        )
        return _json_error(f'Tenant registration failed. Please try again later. Ref: {error_ref}', status=500)
    
    return JsonResponse({
        'success': True,
        'tenant': {
            'id': tenant.id,
            'name': tenant.name,
            'subdomain': tenant.subdomain,
            'domain': tenant.primary_domain_url,
            'path_url': _tenant_url('service:welcome', tenant=tenant),
            'system_url': _tenant_url('service:system_demo', tenant=tenant),
            'paid_until': str(tenant.paid_until),
            'is_trial': tenant.is_trial,
        },
        'owner': {
            'id': owner.id,
            'email': owner.email,
        }
    })


@csrf_exempt
@require_http_methods(["POST"])
def api_owner_admin_login(request, *args, **kwargs):
    """Verify owner credentials for tenant-scoped admin access.
    Only allows login using the credentials of the tenant owner for that specific tenant.
    """
    if _rate_limit(request, 'owner_admin_login', limit=8, window_seconds=60):
        return _json_error('Too many attempts. Please wait a minute and try again.', status=429)
    data = _parse_json_body(request)
    if data is None:
        return _json_error('Invalid JSON')

    email = data.get('email', '').strip()
    password = data.get('password', '')
    tenant_slug = data.get('tenant_slug', '').strip()

    if not email or not password:
        return _json_error('Email and password are required')

    if not tenant_slug:
        return _json_error('Tenant slug is required')

    # Step 1: Verify owner exists in database
    owner = _get_owner_by_email(email)
    if not owner:
        return _json_error('Invalid email or password. Please try again.', status=401)

    # Step 2: Verify password
    if not check_password(password, owner.password):
        return _json_error('Invalid email or password. Please try again.', status=401)

    # Step 3: Check if specified tenant exists (case-insensitive for usability)
    from customers.models import CRTenant
    tenant = CRTenant.objects.filter(subdomain__iexact=tenant_slug, is_active=True).first()
    if not tenant:
        return _json_error('Invalid tenant. Please check your tenant slug and try again.', status=400)

    # Step 4: Verify owner owns this specific tenant
    if tenant.owner_id != owner.id:
        return _json_error('This is not your tenant. Please log in to your own tenant.', status=403)

    # Step 5: Success - owner owns this tenant
    request.session.cycle_key()
    request.session['service_user'] = {
        'user_type': 'owner',
        'owner_id': owner.id,
        'email': owner.email,
        'program_name': owner.program_name,
    }
    request.session['tenant_id'] = tenant.id
    request.session['program_name'] = tenant.name
    request.session['tenant_subdomain'] = tenant.subdomain
    request.session['tenant_key'] = tenant.tenant_key

    return JsonResponse({
        'success': True,
        'message': f'Successfully logged in to admin for tenant: {tenant.name}',
        'owner': {
            'id': owner.id,
            'email': owner.email,
            'tenant': {
                'id': tenant.id,
                'name': tenant.name,
                'subdomain': tenant.subdomain,
            }
        },
        'session': _build_owner_admin_session_payload(owner, tenant),
    })


@require_http_methods(["GET"])
def api_owner_admin_session(request, *args, **kwargs):
    user = request.session.get('service_user') or {}
    if user.get('user_type') != 'owner':
        return JsonResponse({'success': True, 'authenticated': False})

    owner_id = user.get('owner_id')
    tenant = _get_tenant_from_request(request)
    if not owner_id or not tenant:
        return JsonResponse({'success': True, 'authenticated': False})

    owner = _get_owner_by_id(owner_id)
    if not owner:
        return JsonResponse({'success': True, 'authenticated': False})

    if getattr(tenant, 'owner_id', None) != owner.id or not getattr(tenant, 'is_active', False):
        return JsonResponse({'success': True, 'authenticated': False})

    return JsonResponse({
        'success': True,
        'authenticated': True,
        'session': _build_owner_admin_session_payload(owner, tenant),
    })


@require_http_methods(["POST"])
def api_owner_admin_logout(request, *args, **kwargs):
    _clear_service_session(request)
    return JsonResponse({'success': True, 'authenticated': False})


@require_http_methods(["GET", "PUT"])
def api_owner_vault(request, *args, **kwargs):
    user = request.session.get('service_user') or {}
    if user.get('user_type') != 'owner':
        return _json_error('Owner authentication required', status=401)

    owner_id = user.get('owner_id')
    if not owner_id:
        return _json_error('Owner authentication required', status=401)

    owner = _get_owner_by_id(owner_id)
    if not owner:
        _clear_service_session(request)
        return _json_error('Owner account not found', status=404)

    setting = SystemSetting.objects.filter(
        setting_key=OWNER_VAULT_NOTE_KEY,
        created_by=owner.id,
    ).first()

    if request.method == 'GET':
        return JsonResponse({
            'success': True,
            'data': _owner_vault_payload(setting),
        })

    try:
        body = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return _json_error('Invalid JSON payload')

    vault_note = str(body.get('vault_note') or '').strip()
    if not vault_note:
        return _json_error('Please paste the account information you want to store')

    if setting is None:
        setting = SystemSetting.objects.create(
            setting_key=OWNER_VAULT_NOTE_KEY,
            setting_value=vault_note,
            setting_type='string',
            description='Stored owner file manager access note',
            created_by=owner.id,
            updated_by=owner.id,
        )
    else:
        setting.setting_value = vault_note
        setting.setting_type = 'string'
        setting.updated_by = owner.id
        setting.save(update_fields=['setting_value', 'setting_type', 'updated_by', 'updated_at'])

    return JsonResponse({
        'success': True,
        'message': 'Your external file manager note was stored successfully.',
        'data': _owner_vault_payload(setting),
    })


@require_http_methods(["POST"])
def api_owner_vault_reveal(request, *args, **kwargs):
    user = request.session.get('service_user') or {}
    if user.get('user_type') != 'owner':
        return _json_error('Owner authentication required', status=401)

    owner_id = user.get('owner_id')
    if not owner_id:
        return _json_error('Owner authentication required', status=401)

    owner = _get_owner_by_id(owner_id)
    if not owner:
        _clear_service_session(request)
        return _json_error('Owner account not found', status=404)

    try:
        body = json.loads(request.body or '{}')
    except json.JSONDecodeError:
        return _json_error('Invalid JSON payload')

    password = body.get('password', '')
    if not password:
        return _json_error('Please enter your current owner password')

    if not check_password(password, owner.password):
        return _json_error('Your system login credential is incorrect', status=403)

    setting = SystemSetting.objects.filter(
        setting_key=OWNER_VAULT_NOTE_KEY,
        created_by=owner.id,
    ).first()

    if not setting or not (setting.setting_value or '').strip():
        return _json_error('No stored file manager note was found for this owner', status=404)

    return JsonResponse({
        'success': True,
        'data': {
            'vault_note': setting.setting_value,
            'updated_at': setting.updated_at.isoformat() if setting.updated_at else None,
        }
    })


@require_http_methods(["POST"])
def api_create_member(request, *args, **kwargs):
    """API endpoint to create a member"""
    data = _parse_json_body(request)
    if data is None:
        return _json_error('Invalid JSON')
    
    # Check authentication
    user = request.session.get('service_user')
    if not user or user.get('user_type') != 'owner':
        return _json_error('Authentication required', status=401)
    
    reg_number = data.get('reg_number', '').strip()
    program_name = data.get('program_name', '').strip()
    password = data.get('password', '')
    
    if not all([reg_number, program_name, password]):
        return _json_error('All fields required')
    
    owner = _get_owner_by_id(user['owner_id'], include_inactive=True)
    if not owner:
        _clear_service_session(request)
        return _json_error('Owner account not found', status=404)

    core_user = _get_owner_core_user(owner, reg_number)
    if not core_user:
        return _json_error('This registration number must already exist in your main system records')
    
    if Member.objects.filter(reg_number=reg_number).exists():
        return _json_error('Registration number exists')
    
    member = Member.objects.create(
        owner=owner,
        reg_number=reg_number,
        program_name=program_name,
        password=make_password(password),
        is_active=True,
    )
    
    return JsonResponse({
        'success': True,
        'member': {
            'id': member.id,
            'reg_number': member.reg_number,
            'program_name': member.program_name,
        }
    })


@require_http_methods(["POST"])
def api_post_comment(request, *args, **kwargs):
    """API endpoint to post a comment"""
    data = _parse_json_body(request)
    if data is None:
        return _json_error('Invalid JSON')
    
    user = request.session.get('service_user')
    if not user or user.get('user_type') != 'member':
        return _json_error('Authentication required', status=401)
    
    content = data.get('content', '').strip()
    rating = data.get('rating', 5)
    
    if not content:
        return _json_error('Content required')
    
    try:
        member = _get_member_by_id(user['member_id'])
        rating_value = min(max(int(rating), 1), 5)
    except (TypeError, ValueError):
        member = None
    if not member:
        return _json_error('Invalid comment submission')
    
    comment = Comment.objects.create(
        member=member,
        owner_id=member.owner_id,
        content=content,
        rating=rating_value,
        status='pending',
    )
    
    return JsonResponse({
        'success': True,
        'comment': {
            'id': comment.id,
            'status': comment.status,
            'message': 'Comment submitted for moderation'
        }
    })


@require_http_methods(["POST"])
def api_moderate_comment(request, *args, **kwargs):
    """API endpoint to moderate a comment"""
    data = _parse_json_body(request)
    if data is None:
        return _json_error('Invalid JSON')
    
    user = request.session.get('service_user')
    if not user or user.get('user_type') != 'owner':
        return _json_error('Authentication required', status=401)
    
    comment_id = data.get('comment_id')
    action = data.get('action')  # 'approve', 'reject', 'delete'
    
    if not comment_id or action not in ['approve', 'reject', 'delete']:
        return _json_error('Invalid parameters')
    
    owner = _get_owner_by_id(user['owner_id'], include_inactive=True)
    if not owner:
        _clear_service_session(request)
        return _json_error('Owner account not found', status=404)
    
    try:
        comment = Comment.objects.get(id=comment_id, owner=owner)
    except Comment.DoesNotExist:
        return _json_error('Comment not found', status=404)
    
    if action == 'approve':
        comment.status = 'approved'
        comment.save()
    elif action == 'reject':
        comment.status = 'rejected'
        comment.save()
    elif action == 'delete':
        comment.delete()
    
    return JsonResponse({
        'success': True,
        'message': f'Comment {action}d successfully'
    })


def subscription_expired(request, *args, **kwargs):
    """Show subscription expired page"""
    user = request.session.get('service_user')
    tenant = _get_tenant_from_request(request)
    
    return render(request, 'service/subscription_expired.html', {
        'tenant': tenant,
        'user': user,
    })
