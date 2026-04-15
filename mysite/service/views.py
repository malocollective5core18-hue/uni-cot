"""
Service views updated for multi-tenant SaaS platform.
Includes tenant-aware views and API endpoints.
"""

import json
import logging
import uuid
from functools import wraps
from time import time

from django.contrib import messages
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from .models import OwnerUser, Member, Comment
from core.models import User as CoreUser
from customers.models import create_owner_tenant, get_base_domain


logger = logging.getLogger(__name__)


def _json_error(message, status=400):
    return JsonResponse({'error': message}, status=status)


def _build_access_domain(domain):
    if not domain:
        return None
    if domain.endswith('.localhost') and ':' not in domain:
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
            try:
                owner = OwnerUser.objects.get(id=user.get('owner_id'))
                tenant = getattr(owner, 'tenant', None)
            except OwnerUser.DoesNotExist:
                pass
        elif user.get('user_type') == 'member':
            try:
                member = Member.objects.select_related('owner__tenant').get(id=user.get('member_id'))
                tenant = getattr(member.owner, 'tenant', None)
            except Member.DoesNotExist:
                pass
    
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
    member_count = 0
    comment_count = 0
    pending_comments = 0
    
    if user:
        user_type = user.get('user_type')
        owner_id = user.get('owner_id')
        
        if user_type == 'owner':
            try:
                owner = OwnerUser.objects.select_related('tenant').get(id=owner_id)
                if tenant:
                    program_name = tenant.name
                else:
                    program_name = owner.program_name
                    # Try to get tenant
                    tenant = getattr(owner, 'tenant', None)
                
                if tenant:
                    member_count = Member.objects.filter(owner=owner).count()
                    comment_count = Comment.objects.filter(owner=owner).count()
                    pending_comments = Comment.objects.filter(owner=owner, status='pending').count()
                    comments = Comment.objects.filter(owner=owner, status='approved').prefetch_related('replies')[:10]
                else:
                    # Fallback for non-tenant owners
                    member_count = Member.objects.filter(owner=owner).count()
                    comment_count = Comment.objects.filter(owner=owner).count()
                    pending_comments = Comment.objects.filter(owner=owner, status='pending').count()
                    comments = Comment.objects.filter(owner=owner, status='approved').prefetch_related('replies')[:10]
            except OwnerUser.DoesNotExist:
                pass
        elif user_type == 'member':
            try:
                member = Member.objects.select_related('owner', 'owner__tenant').get(id=user.get('member_id'))
                program_name = member.program_name
                owner = member.owner
                tenant = getattr(owner, 'tenant', None)
                comments = Comment.objects.filter(owner=owner, status='approved').prefetch_related('replies')[:10]
            except Member.DoesNotExist:
                pass
    else:
        # Guest user - show approved comments
        if tenant:
            # Tenant-specific comments
            owner = getattr(tenant, 'owner', None)
            if owner:
                comments = Comment.objects.filter(owner=owner, status='approved').prefetch_related('replies')[:10]
        else:
            # Public demo - do not leak owner data
            comments = []
    
    return render(request, 'index.html', {
        'program_name': program_name,
        'user': user,
        'comments': comments,
        'member_count': member_count,
        'comment_count': comment_count,
        'pending_comments': pending_comments,
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
        try:
            owner = OwnerUser.objects.select_related('tenant').get(id=user['owner_id'])
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
        except OwnerUser.DoesNotExist:
            pass
    elif user and user.get('user_type') == 'member':
        try:
            member = Member.objects.select_related('owner__tenant').get(id=user['member_id'])
            context.update({
                'member': member,
                'program_name': member.program_name,
            })
        except Member.DoesNotExist:
            pass
    
    # Get comments based on tenant
    if tenant and tenant.owner:
        comments = Comment.objects.filter(
            owner=tenant.owner, 
            status='approved'
        ).prefetch_related('replies', 'member')[:10]
    else:
        comments = []
    
    context['comments'] = comments
    context['tenant_base_path'] = _tenant_base_path(request=request, tenant=tenant)
    
    return render(request, 'service/welcome.html', context)


def system_demo(request, *args, **kwargs):
    """Serve the actual product shell; data isolation is handled by tenant-scoped APIs."""
    tenant = _get_tenant_from_request(request)
    if tenant:
        user = request.session.get('service_user') or {}
        if user.get('user_type') != 'owner' or user.get('owner_id') != getattr(tenant, 'owner_id', None):
            messages.error(request, 'Please log in as the owner to access this system.')
            return _tenant_redirect(request, 'service:welcome', tenant=tenant)
    return render(request, 'system_index.html', {
        'tenant': tenant,
        'tenant_base_path': _tenant_base_path(request=request, tenant=tenant),
    })


def login_view(request, *args, **kwargs):
    """Login view - handles both owner and member login"""
    if request.method == 'POST':
        if _rate_limit(request, 'login', limit=5, window_seconds=60):
            messages.error(request, 'Too many login attempts. Please wait a minute and try again.')
            return _tenant_redirect(request, 'service:welcome')
        tenant = _get_tenant_from_request(request)
        login_identifier = request.POST.get('login_identifier', '').strip()
        password = request.POST.get('password', '')
        
        if '@' in login_identifier:
            # Owner login
            owner = OwnerUser.objects.filter(email=login_identifier, is_active=True).first()
            if owner:
                owner_tenant = getattr(owner, 'tenant', None)
                if tenant and owner_tenant and owner_tenant.id != tenant.id:
                    messages.error(request, 'This owner account belongs to a different domain.')
                    return _tenant_redirect(request, 'service:welcome')
            if owner and check_password(password, owner.password):
                request.session.flush()
                request.session.cycle_key()
                request.session['service_user'] = {
                    'user_type': 'owner',
                    'owner_id': owner.id,
                    'email': owner.email,
                    'program_name': owner.program_name,
                }
                request.session['program_name'] = owner.program_name
                
                # Link tenant to session if exists
                tenant = getattr(owner, 'tenant', None)
                if tenant:
                    request.session['tenant_id'] = tenant.id
                    request.session['tenant_subdomain'] = tenant.subdomain
                    request.session['tenant_key'] = tenant.tenant_key
                
                messages.success(request, f'Welcome back, {owner.email}!')
                return _tenant_redirect(request, 'service:owner_dashboard', tenant=tenant)
            else:
                messages.error(request, 'Invalid email or password.')
        else:
            # Member login
            if not tenant:
                messages.error(request, 'Members must log in from their owner domain.')
                return _tenant_redirect(request, 'service:welcome')
            reg_number = login_identifier
            member = Member.objects.filter(reg_number=reg_number, is_active=True).first()
            if member:
                member_tenant = getattr(member.owner, 'tenant', None)
                if member_tenant and tenant and member_tenant.id != tenant.id:
                    messages.error(request, 'This member account belongs to a different domain.')
                    return _tenant_redirect(request, 'service:welcome')
                if not _get_owner_core_user(member.owner, reg_number):
                    messages.error(request, 'This member must first exist in the owner system records.')
                    return _tenant_redirect(request, 'service:welcome')
            if member and check_password(password, member.password):
                request.session.flush()
                request.session.cycle_key()
                request.session['service_user'] = {
                    'user_type': 'member',
                    'member_id': member.id,
                    'reg_number': member.reg_number,
                    'program_name': member.program_name,
                    'owner_id': member.owner.id,
                }
                request.session['program_name'] = member.program_name
                
                # Link tenant to session
                tenant = getattr(member.owner, 'tenant', None)
                if tenant:
                    request.session['tenant_id'] = tenant.id
                    request.session['tenant_subdomain'] = tenant.subdomain
                    request.session['tenant_key'] = tenant.tenant_key
                
                messages.success(request, f'Welcome back, {member.reg_number}!')
                return _tenant_redirect(request, 'service:member_dashboard', tenant=tenant)
            else:
                messages.error(request, 'Invalid registration number or password.')
    
    return _tenant_redirect(request, 'service:welcome')


def register_view(request, *args, **kwargs):
    """Owner registration view with tenant creation"""
    if request.method == 'POST':
        if _rate_limit(request, 'register', limit=5, window_seconds=300):
            messages.error(request, 'Too many registration attempts. Please wait a few minutes and try again.')
            return _tenant_redirect(request, 'service:welcome')
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
    return _tenant_redirect(request, 'service:welcome')


@service_login_required
@service_role_required('owner')
def owner_dashboard(request, *args, **kwargs):
    """Owner dashboard - full access, stats, moderation with tenant info"""
    user = request.session.get('service_user')
    
    try:
        owner = OwnerUser.objects.select_related('tenant').get(id=user.get('owner_id'))
    except OwnerUser.DoesNotExist:
        return _tenant_redirect(request, 'service:welcome')
    
    # Get tenant info
    tenant = getattr(owner, 'tenant', None)

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
        member = Member.objects.select_related('owner__tenant').get(id=user.get('member_id'))
    except Member.DoesNotExist:
        return _tenant_redirect(request, 'service:welcome')
    
    comments = Comment.objects.filter(owner=member.owner, status='approved')
    
    return render(request, 'member_dashboard.html', {
        'member': member,
        'comments': comments,
        'tenant_base_path': _tenant_base_path(request=request, tenant=getattr(member.owner, 'tenant', None)),
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
        member = Member.objects.get(id=user.get('member_id'))
        rating_value = min(max(int(rating), 1), 5)
    except (Member.DoesNotExist, TypeError, ValueError):
        messages.error(request, 'Invalid comment submission.')
        return _tenant_redirect(request, 'service:welcome')
    
    Comment.objects.create(
        member=member,
        owner=member.owner,
        content=content,
        rating=rating_value,
        status='pending',
    )
    
    messages.success(request, 'Comment submitted for moderation.')
    return _tenant_redirect(request, 'service:welcome')


@require_http_methods(["POST"])
def register_member(request, *args, **kwargs):
    """Register a new member - only owners can do this"""
    user = request.session.get('service_user')
    if not user or user.get('user_type') != 'owner':
        messages.error(request, 'Only owners can register members.')
        return _tenant_redirect(request, 'service:welcome')
    
    owner = OwnerUser.objects.filter(id=user.get('owner_id')).first()
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
    owner = OwnerUser.objects.get(id=user.get('owner_id'))
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


@require_http_methods(["POST"])
def api_owner_admin_login(request, *args, **kwargs):
    """Verify owner credentials for tenant-scoped admin access."""
    if _rate_limit(request, 'owner_admin_login', limit=8, window_seconds=60):
        return _json_error('Too many attempts. Please wait a minute and try again.', status=429)
    data = _parse_json_body(request)
    if data is None:
        return _json_error('Invalid JSON')

    email = data.get('email', '').strip()
    password = data.get('password', '')

    if not email or not password:
        return _json_error('Email and password are required')

    tenant = _get_tenant_from_request(request)
    if not tenant or not getattr(tenant, 'owner', None):
        return _json_error('Admin login is only available on your owner domain', status=403)

    owner = tenant.owner
    if not owner.is_active:
        return _json_error('Owner account is not active', status=403)

    if owner.email.lower() != email.lower():
        return _json_error('This email does not belong to this domain', status=403)

    if not check_password(password, owner.password):
        return _json_error('Invalid email or password', status=401)

    return JsonResponse({
        'success': True,
        'owner': {
            'id': owner.id,
            'email': owner.email,
            'program_name': owner.program_name,
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
    
    owner = OwnerUser.objects.get(id=user['owner_id'])
    
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
        member = Member.objects.get(id=user['member_id'])
        rating_value = min(max(int(rating), 1), 5)
    except (Member.DoesNotExist, TypeError, ValueError):
        return _json_error('Invalid comment submission')
    
    comment = Comment.objects.create(
        member=member,
        owner=member.owner,
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
    
    owner = OwnerUser.objects.get(id=user['owner_id'])
    
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
