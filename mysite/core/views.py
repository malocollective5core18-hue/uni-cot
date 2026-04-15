from django.shortcuts import redirect, render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.core.exceptions import ObjectDoesNotExist
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Avg, Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from datetime import datetime
import json
import logging

from .models import CountdownCard, ImagePost, Property, User, UserGroup, UserGroupMember, SystemSetting, RegistrationFormField, ExternalTable, ExternalTableRecord
from customers.models import (
    CRTenant,
    Domain,
    TenantSubscription,
    create_tenant_subscription,
    resolve_subscription_status,
)
from service.models import Comment, OwnerUser


logger = logging.getLogger(__name__)


def _build_access_domain(domain):
    if not domain:
        return None
    if domain.endswith('.localhost') and ':' not in domain:
        return f'{domain}:8000'
    return domain


def _get_product_owner_id(request):
    tenant = getattr(request, 'tenant', None)
    if tenant and getattr(tenant, 'owner_id', None):
        return tenant.owner_id
    return None


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
    owner_id = _get_product_owner_id(request)
    queryset = ExternalTable.objects.all()
    if owner_id is not None:
        queryset = queryset.filter(created_by=owner_id)
    return queryset


def normalize_record(schema, data):
    """
    Normalize record data against current schema.
    Adds missing fields with None value if schema defines them.
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


def _sync_external_table_record_count(table):
    table.record_count = ExternalTableRecord.objects.filter(table=table).count()
    table.save(update_fields=['record_count', 'updated_at'])

# Create your views here.
def index(request):
    # Tenant domains should land on the system home, not the public welcome page
    if getattr(request, 'tenant', None):
        return redirect('/system/')
    return render(request, 'index.html')

def groups(request):
    return render(request, 'groups.html')

def properties(request):
    return render(request, 'properties.html')

def external_tables(request):
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
    return render(request, 'external_tables.html', {
        'framework_fields': framework_fields,
    })


@staff_member_required
def founder_saas_system_control(request):
    if request.method == 'POST':
        action = request.POST.get('action', '').strip()
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
                defaults={
                    'tenant': tenant,
                    'is_primary': make_primary,
                },
            )

            tenant.is_active = True
            tenant.save(update_fields=['is_active'])

            if created:
                messages.success(request, f'Custom domain {custom_domain} activated for {tenant.name}.')
            else:
                messages.success(request, f'Custom domain {custom_domain} updated for {tenant.name}.')
            return redirect('founder_saas_system_control')

        messages.error(request, 'Unknown founder action.')
        return redirect('founder_saas_system_control')

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
    total_reviews = Comment.objects.count()
    pending_reviews = Comment.objects.filter(status='pending').count()
    owner_rows = []

    tenants = []
    tenant_qs = CRTenant.objects.select_related('owner').prefetch_related('domains', 'subscriptions').order_by('-created_on')
    for tenant in tenant_qs:
        owner = tenant.owner
        owner_comments = Comment.objects.filter(owner=owner) if owner else Comment.objects.none()
        tenant.latest_subscription = tenant.subscriptions.filter(is_active=True).order_by('-created_at').first()
        tenant.all_domains = list(tenant.domains.order_by('-is_primary', 'domain'))
        tenant.member_count = owner.members.count() if owner else 0
        tenant.review_count = owner_comments.count()
        tenant.pending_review_count = owner_comments.filter(status='pending').count()
        tenant.avg_rating = owner_comments.aggregate(avg=Avg('rating'))['avg']
        tenant.access_domain = _build_access_domain(tenant.primary_domain_url or f'{tenant.subdomain}.localhost')
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
            'plan': tenant.latest_subscription.plan if tenant.latest_subscription else ('trial' if tenant.is_trial else 'basic'),
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
        'total_reviews': total_reviews,
        'pending_reviews': pending_reviews,
        'tenants': tenants,
        'owner_rows': owner_rows,
        'reviews': reviews,
        'review_signals': review_signals,
        'today': timezone.now().date(),
    }
    return render(request, 'admin_only/founder_SAAS_system_control.html', context)

# API Views for Image Posts (Slider Images)
def api_slider_images(request):
    """
    GET: List all active image posts
    POST: Create a new image post
    """
    if request.method == 'GET':
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
        return JsonResponse({'success': True, 'data': data})
    
    elif request.method == 'POST':
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
                }
            }, status=201)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

def api_slider_image_detail(request, image_id):
    """
    GET: Get single image details
    PUT: Update an image
    DELETE: Delete an image
    """
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
            }
        })
    
    elif request.method == 'PUT':
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
                }
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    
    elif request.method == 'DELETE':
        image.delete()
        return JsonResponse({'success': True, 'message': 'Image deleted successfully'})


# API Views for Countdown Cards
def api_countdown_cards(request):
    """
    GET: List all active countdown cards
    POST: Create a new countdown card
    """
    if request.method == 'GET':
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
        return JsonResponse({'success': True, 'data': data})
    
    elif request.method == 'POST':
        try:
            body = json.loads(request.body)
            
            # Parse datetime strings
            start_time_str = body.get('start_time')
            end_time_str = body.get('end_time')
            
            start_time = None
            end_time = None
            
            if start_time_str:
                # Handle ISO format strings with 'Z' suffix
                if isinstance(start_time_str, str):
                    if start_time_str.endswith('Z'):
                        start_time_str = start_time_str[:-1] + '+00:00'
                    start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
            
            if end_time_str:
                if isinstance(end_time_str, str):
                    if end_time_str.endswith('Z'):
                        end_time_str = end_time_str[:-1] + '+00:00'
                    end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
            
            card = CountdownCard.objects.create(
                title=body.get('title', ''),
                description=body.get('description', ''),
                file_url=body.get('file_url', ''),
                start_time=start_time,
                end_time=end_time,
                created_by=str(_get_product_owner_id(request) or body.get('created_by', PUBLIC_DEMO_COUNTDOWN_KEY)),
            )
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
                }
            }, status=201)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)


def api_countdown_card_detail(request, card_id):
    """
    GET: Get single countdown card
    PUT: Update a countdown card
    DELETE: Delete a countdown card
    """
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
            }
        })
    
    elif request.method == 'PUT':
        try:
            body = json.loads(request.body)
            card.title = body.get('title', card.title)
            card.description = body.get('description', card.description)
            card.file_url = body.get('file_url', card.file_url)
            if 'start_time' in body:
                start_time_str = body['start_time']
                if isinstance(start_time_str, str):
                    if start_time_str.endswith('Z'):
                        start_time_str = start_time_str[:-1] + '+00:00'
                    card.start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
            if 'end_time' in body:
                end_time_str = body['end_time']
                if isinstance(end_time_str, str):
                    if end_time_str.endswith('Z'):
                        end_time_str = end_time_str[:-1] + '+00:00'
                    card.end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
            if 'status' in body:
                card.status = body['status']
            if 'is_published' in body:
                card.is_published = body['is_published']
            card.save()
            return JsonResponse({
                'success': True,
                'message': 'Countdown card updated successfully',
                'data': {
                    'id': card.id,
                    'title': card.title,
                    'description': card.description,
                    'file_url': card.file_url,
                    'status': card.status,
                }
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    
    elif request.method == 'DELETE':
        card.delete()
        return JsonResponse({'success': True, 'message': 'Countdown card deleted successfully'})


# ============================================
# API Views for Properties (Lost & Found)
# ============================================

def api_properties(request):
    """
    GET: List all properties (lost/found items)
    POST: Create a new property
    """
    if request.method == 'GET':
        category = request.GET.get('category', None)
        status = request.GET.get('status', None)
        
        properties = _scope_properties_queryset(request)
        if category:
            properties = properties.filter(category=category)
        if status:
            properties = properties.filter(status=status)
        
        data = [{
            'id': prop.id,
            'item_name': prop.item_name,
            'description': prop.description,
            'category': prop.category,
            'location': prop.location or '',
            'date_found': prop.date_found.isoformat() if prop.date_found else None,
            'image_url': prop.image_url or '',
            'contact_info': prop.contact_info or '',
            'status': prop.status,
            'claimed_by': prop.claimed_by,
            'created_at': prop.created_at.isoformat() if prop.created_at else None,
        } for prop in properties]
        return JsonResponse({'success': True, 'data': data})
    
    elif request.method == 'POST':
        try:
            body = json.loads(request.body)

            # Handle flexible date formats
            date_found_raw = body.get('date_found')
            date_found = None
            if date_found_raw:
                try:
                    # Try full ISO date/time first
                    date_found = datetime.fromisoformat(date_found_raw)
                except Exception:
                    try:
                        # Try date-only string as midnight
                        date_found = datetime.fromisoformat(date_found_raw + 'T00:00:00')
                    except Exception:
                        date_found = None

            prop = Property.objects.create(
                item_name=body.get('item_name', ''),
                description=body.get('description', ''),
                category=body.get('category', 'lost'),
                location=body.get('location', ''),
                date_found=date_found,
                image_url=body.get('image_url', ''),
                contact_info=body.get('contact_info', ''),
                reported_by=body.get('reported_by'),
                created_by=_get_product_owner_id(request),
            )
            return JsonResponse({
                'success': True,
                'message': 'Property created successfully',
                'data': {
                    'id': prop.id,
                    'item_name': prop.item_name,
                    'description': prop.description,
                    'category': prop.category,
                    'status': prop.status,
                }
            }, status=201)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)


def api_property_detail(request, property_id):
    """
    GET: Get single property
    PUT: Update a property
    DELETE: Delete a property
    """
    try:
        prop = _scope_properties_queryset(request).get(id=property_id)
    except ObjectDoesNotExist:
        return JsonResponse({'success': False, 'error': 'Property not found'}, status=404)
    
    if request.method == 'GET':
        return JsonResponse({
            'success': True,
            'data': {
                'id': prop.id,
                'item_name': prop.item_name,
                'description': prop.description,
                'category': prop.category,
                'location': prop.location or '',
                'date_found': prop.date_found.isoformat() if prop.date_found else None,
                'image_url': prop.image_url or '',
                'contact_info': prop.contact_info or '',
                'status': prop.status,
                'claimed_by': prop.claimed_by,
                'created_at': prop.created_at.isoformat() if prop.created_at else None,
            }
        })
    
    elif request.method == 'PUT':
        try:
            body = json.loads(request.body)
            prop.item_name = body.get('item_name', prop.item_name)
            prop.description = body.get('description', prop.description)
            prop.category = body.get('category', prop.category)
            prop.location = body.get('location', prop.location)
            if 'date_found' in body:
                prop.date_found = body['date_found']
            prop.image_url = body.get('image_url', prop.image_url)
            prop.contact_info = body.get('contact_info', prop.contact_info)
            if 'status' in body:
                prop.status = body['status']
            if 'claimed_by' in body:
                prop.claimed_by = body['claimed_by']
            prop.save()
            return JsonResponse({
                'success': True,
                'message': 'Property updated successfully',
                'data': {
                    'id': prop.id,
                    'item_name': prop.item_name,
                    'description': prop.description,
                    'category': prop.category,
                    'status': prop.status,
                }
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    
    elif request.method == 'DELETE':
        prop.delete()
        return JsonResponse({'success': True, 'message': 'Property deleted successfully'})


# ============================================
# API Views for Users
# ============================================

def api_users(request):
    """
    GET: List all users
    POST: Create a new user
    """
    if request.method == 'GET':
        users = _scope_users_queryset(request).filter(is_active=True)
        data = [{
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
        } for user in users]
        return JsonResponse({'success': True, 'data': data})
    
    elif request.method == 'POST':
        try:
            try:
                body = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse({'success': False, 'error': 'Invalid JSON in request body'}, status=400)
            
            # Normalize field names for flexibility from different forms
            if 'full_name' not in body and 'name' in body:
                body['full_name'] = body.get('name', '').strip()
            if 'full_name' not in body and 'fullName' in body:
                body['full_name'] = body.get('fullName', '').strip()
            if 'full_name' not in body and 'display_name' in body:
                body['full_name'] = body.get('display_name', '').strip()

            if 'registration_number' not in body and 'reg_no' in body:
                body['registration_number'] = body.get('reg_no', '').strip()
            if 'registration_number' not in body and 'regNo' in body:
                body['registration_number'] = body.get('regNo', '').strip()
            if 'registration_number' not in body and 'regNumber' in body:
                body['registration_number'] = body.get('regNumber', '').strip()

            # Validate required fields
            full_name = body.get('full_name', '').strip()
            registration_number = body.get('registration_number', '').strip()
            
            if not full_name:
                return JsonResponse({'success': False, 'error': 'full_name is required (provide full_name, name or fullName)'}, status=400)
            if not registration_number:
                return JsonResponse({'success': False, 'error': 'registration_number is required (provide registration_number, reg_no or regNo)'}, status=400)
            
            # Check for duplicate registration_number
            if _scope_users_queryset(request).filter(registration_number=registration_number).exists():
                return JsonResponse({'success': False, 'error': 'A user with this registration number already exists'}, status=400)
            
            # Validate email format if provided
            email = body.get('email', '').strip()
            if email and '@' not in email:
                return JsonResponse({'success': False, 'error': 'Invalid email format'}, status=400)
            
            # Convert empty string to None to avoid unique constraint violation
            # (empty strings are considered duplicates, but NULL values are not)
            if not email:
                email = None

            # Handle duplicate email gracefully (no interrupt when email belongs to existing user)
            existing_email_user = None
            if email:
                existing_email_user = _scope_users_queryset(request).filter(email=email).exclude(registration_number=registration_number).first()
            
            if existing_email_user:
                # Keep working, but avoid duplicate unique constraint, and we can treat this as update
                existing_email_user.full_name = full_name
                existing_email_user.registration_number = registration_number
                existing_email_user.phone = body.get('phone', '').strip() or existing_email_user.phone
                existing_email_user.role = body.get('role', 'member')
                existing_email_user.save()
                return JsonResponse({'success': True, 'message': 'User updated (duplicate email used)', 'data': {
                    'id': existing_email_user.id,
                    'full_name': existing_email_user.full_name,
                    'registration_number': existing_email_user.registration_number,
                    'email': existing_email_user.email,
                }}, status=200)
            
            user = User.objects.create(
                full_name=full_name,
                registration_number=registration_number,
                email=email,
                phone=body.get('phone', '').strip() or None,
                role=body.get('role', 'member'),
                created_by=_get_product_owner_id(request),
            )
            return JsonResponse({
                'success': True,
                'message': 'User created successfully',
                'data': {
                    'id': user.id,
                    'full_name': user.full_name,
                    'registration_number': user.registration_number,
                }
            }, status=201)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)


def api_user_detail(request, user_id):
    """
    GET: Get single user
    PUT: Update a user
    DELETE: Delete a user
    """
    try:
        user = _scope_users_queryset(request).get(id=user_id)
    except ObjectDoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'}, status=404)
    
    if request.method == 'GET':
        return JsonResponse({
            'success': True,
            'data': {
                'id': user.id,
                'full_name': user.full_name,
                'registration_number': user.registration_number,
                'email': user.email or '',
                'phone': user.phone or '',
                'status': user.status,
                'role': user.role,
                'group_name': user.group_name or '',
                'is_verified': user.is_verified,
                'last_login': user.last_login.isoformat() if user.last_login else None,
            }
        })
    
    elif request.method == 'PUT':
        try:
            try:
                body = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse({'success': False, 'error': 'Invalid JSON in request body'}, status=400)
            
            # Validate required fields if they are being updated
            if 'full_name' in body:
                full_name = body['full_name'].strip()
                if not full_name:
                    return JsonResponse({'success': False, 'error': 'full_name cannot be empty'}, status=400)
                user.full_name = full_name
            
            if 'registration_number' in body:
                registration_number = body['registration_number'].strip()
                if not registration_number:
                    return JsonResponse({'success': False, 'error': 'registration_number cannot be empty'}, status=400)
                # Check for duplicate registration_number (excluding current user)
                if _scope_users_queryset(request).filter(registration_number=registration_number).exclude(id=user_id).exists():
                    return JsonResponse({'success': False, 'error': 'A user with this registration number already exists'}, status=400)
                user.registration_number = registration_number
            
            if 'email' in body:
                email = body['email'].strip()
                # Validate email format if provided
                if email and '@' not in email:
                    return JsonResponse({'success': False, 'error': 'Invalid email format'}, status=400)
                # Convert empty string to None to avoid unique constraint violation
                if not email:
                    email = None
                # Check for duplicate email if provided (excluding current user)
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
            user.save()
            return JsonResponse({
                'success': True,
                'message': 'User updated successfully',
                'data': {
                    'id': user.id,
                    'full_name': user.full_name,
                    'status': user.status,
                }
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    
    elif request.method == 'DELETE':
        # Perform hard delete so user is removed from the database entirely.
        user.delete()
        return JsonResponse({'success': True, 'message': 'User deleted successfully'})


# ============================================
# API Views for Groups
# ============================================

def api_groups(request):
    """
    GET: List all groups
    POST: Create a new group
    """
    if request.method == 'GET':
        groups = _scope_groups_queryset(request).filter(is_active=True)
        data = [{
            'id': group.id,
            'group_name': group.group_name,
            'group_code': group.group_code or '',
            'description': group.description or '',
            'max_members': group.max_members,
            'current_members': group.current_members,
            'is_flagged': group.is_flagged,
            'created_at': group.created_at.isoformat() if group.created_at else None,
        } for group in groups]
        return JsonResponse({'success': True, 'data': data})
    
    elif request.method == 'POST':
        try:
            body = json.loads(request.body)
            group = UserGroup.objects.create(
                group_name=body.get('group_name', ''),
                group_code=body.get('group_code') or None,
                description=body.get('description', ''),
                max_members=body.get('max_members', 50),
                created_by=_get_product_owner_id(request),
            )
            return JsonResponse({
                'success': True,
                'message': 'Group created successfully',
                'data': {
                    'id': group.id,
                    'group_name': group.group_name,
                }
            }, status=201)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)


def api_group_detail(request, group_id):
    """
    GET: Get single group with members
    PUT: Update a group
    DELETE: Delete a group
    """
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

        # Fallback to user.group_name when dedicated UserGroupMember entries are not present
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
            'data': {
                'id': group.id,
                'group_name': group.group_name,
                'group_code': group.group_code or '',
                'description': group.description or '',
                'max_members': group.max_members,
                'current_members': group.current_members,
                'members': member_list,
            }
        })
    
    elif request.method == 'PUT':
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

            # Keep users tied to group_name in sync
            if old_group_name != new_group_name:
                _scope_users_queryset(request).filter(group_name=old_group_name, is_active=True).update(group_name=new_group_name)

            return JsonResponse({
                'success': True,
                'message': 'Group updated successfully',
                'data': {
                    'id': group.id,
                    'group_name': group.group_name,
                }
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    
    elif request.method == 'DELETE':
        # Clear group_name for all users in this group first
        _scope_users_queryset(request).filter(group_name=group.group_name).update(group_name='')
        # Delete all group members
        _scope_group_members_queryset(request).filter(group_id=group_id).delete()
        # Delete the group
        group.delete()
        return JsonResponse({'success': True, 'message': 'Group deleted successfully'})


def api_group_members(request):
    """
    GET: List all group members
    POST: Add a member to a group
    """
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

        if status is not None:
            members = members.filter(status=status)
        else:
            members = members.filter(status='active')

        data = [{
            'id': m.id,
            'user_id': m.user_id,
            'group_id': m.group_id,
            'is_leader': m.is_leader,
            'status': m.status,
            'joined_at': m.joined_at.isoformat() if m.joined_at else None,
        } for m in members]
        return JsonResponse({'success': True, 'data': data})
    
    elif request.method == 'POST':
        try:
            body = json.loads(request.body)
            user_id = body.get('user_id')
            group_id = body.get('group_id')
            is_leader = body.get('is_leader', False)
            status = body.get('status', 'active')
            
            if not user_id or not group_id:
                return JsonResponse({'success': False, 'error': 'user_id and group_id are required'}, status=400)
            
            # Check if user exists
            try:
                user = _scope_users_queryset(request).get(id=user_id)
            except ObjectDoesNotExist:
                return JsonResponse({'success': False, 'error': 'User not found'}, status=404)
            
            # Check if group exists
            try:
                group = _scope_groups_queryset(request).get(id=group_id)
            except ObjectDoesNotExist:
                return JsonResponse({'success': False, 'error': 'Group not found'}, status=404)
            
            # Check if member already exists
            if _scope_group_members_queryset(request).filter(user_id=user_id, group_id=group_id).exists():
                return JsonResponse({'success': False, 'error': 'User is already a member of this group'}, status=400)
            
            # Create group member record
            member = UserGroupMember.objects.create(
                user_id=user_id,
                group_id=group_id,
                is_leader=is_leader,
                status=status
            )
            
            # Update user's group_name
            user.group_name = group.group_name
            user.save()
            
            # Update group's current_members count
            group.current_members = _scope_group_members_queryset(request).filter(group_id=group_id, status='active').count()
            group.save()
            
            return JsonResponse({
                'success': True,
                'message': 'Member added to group successfully',
                'data': {
                    'id': member.id,
                    'user_id': member.user_id,
                    'group_id': member.group_id,
                    'is_leader': member.is_leader,
                    'status': member.status,
                }
            }, status=201)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    
    else:
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_group_member_detail(request, member_id):
    """
    GET: Get single group member
    PUT: Update a group member
    DELETE: Remove a member from a group
    """
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
            }
        })
    
    elif request.method == 'PUT':
        try:
            body = json.loads(request.body)
            if 'is_leader' in body:
                member.is_leader = body['is_leader']
            if 'status' in body:
                member.status = body['status']
            member.save()
            
            # Update group's current_members count
            group = _scope_groups_queryset(request).get(id=member.group_id)
            group.current_members = _scope_group_members_queryset(request).filter(group_id=member.group_id, status='active').count()
            group.save()
            
            return JsonResponse({
                'success': True,
                'message': 'Member updated successfully',
                'data': {
                    'id': member.id,
                    'user_id': member.user_id,
                    'group_id': member.group_id,
                    'is_leader': member.is_leader,
                    'status': member.status,
                }
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    
    elif request.method == 'DELETE':
        try:
            group_id = member.group_id
            member.delete()
            
            # Update group's current_members count
            group = _scope_groups_queryset(request).get(id=group_id)
            group.current_members = _scope_group_members_queryset(request).filter(group_id=group_id, status='active').count()
            group.save()
            
            return JsonResponse({'success': True, 'message': 'Member removed from group successfully'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    
    else:
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


# ============================================
# API Views for System Settings (Signup Control)
# ============================================

def api_signup_setting(request):
    """
    GET: Get signup_allowed setting
    PUT: Update signup_allowed setting
    """
    SIGNUP_SETTING_KEY = 'signup_allowed'
    
    if request.method == 'GET':
        try:
            # Try to get existing setting
            try:
                setting = _scope_system_settings_queryset(request).get(setting_key=SIGNUP_SETTING_KEY)
                signup_allowed = setting.setting_value == 'true'
            except ObjectDoesNotExist:
                # Create default setting (signup allowed by default)
                setting = SystemSetting.objects.create(
                    setting_key=SIGNUP_SETTING_KEY,
                    setting_value='true',
                    setting_type='boolean',
                    description='Controls whether new members can sign up',
                    created_by=_get_product_owner_id(request),
                )
                signup_allowed = True
            
            return JsonResponse({
                'success': True,
                'data': {
                    'signup_allowed': signup_allowed,
                    'setting_key': SIGNUP_SETTING_KEY
                }
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    
    elif request.method == 'PUT':
        try:
            body = json.loads(request.body)
            signup_allowed = body.get('signup_allowed', True)
            
            # Get or create the setting
            setting, created = SystemSetting.objects.get_or_create(
                setting_key=SIGNUP_SETTING_KEY,
                created_by=_get_product_owner_id(request),
                defaults={
                    'setting_value': 'true' if signup_allowed else 'false',
                    'setting_type': 'boolean',
                    'description': 'Controls whether new members can sign up'
                }
            )
            
            if not created:
                setting.setting_value = 'true' if signup_allowed else 'false'
                setting.save()
            
            return JsonResponse({
                'success': True,
                'message': 'Signup setting updated successfully',
                'data': {
                    'signup_allowed': signup_allowed,
                    'setting_key': SIGNUP_SETTING_KEY
                }
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    
    else:
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


# ============================================
# API Views for System Settings (Storage Links)
# ============================================

def api_system_settings(request):
    """
    GET: Return storage links for system menu buttons
    PUT: Update storage links for system menu buttons
    """
    STORAGE_NOTES_KEY = 'storage_notes_link'
    STORAGE_CALENDAR_KEY = 'storage_calendar_link'
    STORAGE_SUMMARY_KEY = 'storage_summary_link'
    allowed_keys = [STORAGE_NOTES_KEY, STORAGE_CALENDAR_KEY, STORAGE_SUMMARY_KEY]

    def build_settings_payload():
        settings_map = {key: '' for key in allowed_keys}
        settings = _scope_system_settings_queryset(request).filter(setting_key__in=allowed_keys)
        for setting in settings:
            settings_map[setting.setting_key] = setting.setting_value or ''
        return settings_map

    if request.method == 'GET':
        try:
            return JsonResponse({
                'success': True,
                'data': build_settings_payload(),
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    if request.method == 'PUT':
        try:
            body = json.loads(request.body or '{}')
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON payload'}, status=400)

        owner_id = _get_product_owner_id(request)
        descriptions = {
            STORAGE_NOTES_KEY: 'External storage link for notes/past-files',
            STORAGE_CALENDAR_KEY: 'External storage link for calendar',
            STORAGE_SUMMARY_KEY: 'External storage link for UNI-COT.stf',
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
                }
            )
            if not created:
                setting.setting_value = value
                setting.setting_type = 'string'
                setting.description = descriptions.get(key, setting.description)
                setting.updated_by = owner_id
                setting.save()

        return JsonResponse({
            'success': True,
            'message': 'Storage links updated successfully',
            'data': build_settings_payload(),
        })

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


# ============================================
# API Views for Registration Form Fields
# ============================================

def api_registration_fields(request):
    """
    GET: List all active registration form fields
    POST: Create a new registration form field
    """
    if request.method == 'GET':
        owner_id = _get_product_owner_id(request)
        fields = RegistrationFormField.objects.filter(is_active=True)
        if owner_id is not None:
            fields = fields.filter(created_by=owner_id)
        fields = fields.order_by('display_order', 'id')
        data = [{
            'id': field.id,
            'name': field.field_name,
            'key': field.field_key or field.field_name,  # Use field_name as fallback
            'type': field.field_type,
            'label': field.field_label,
            'placeholder': field.placeholder or '',
            'options': field.options or '',
            'required': 'yes' if field.is_required else 'no',
            'order': field.display_order,
        } for field in fields]
        return JsonResponse({'success': True, 'data': data})
    
    elif request.method == 'POST':
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
                }
            }, status=201)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    
    else:
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_registration_field_detail(request, field_id):
    """
    GET: Get single field
    PUT: Update a field
    DELETE: Delete a field
    """
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
            }
        })
    
    elif request.method == 'PUT':
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
            return JsonResponse({
                'success': True,
                'message': 'Field updated successfully',
                'data': {
                    'id': field.id,
                    'name': field.field_name,
                    'key': field.field_key,
                    'type': field.field_type,
                    'label': field.field_label,
                }
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    
    elif request.method == 'DELETE':
        field.delete()
        return JsonResponse({'success': True, 'message': 'Field deleted successfully'})
    
    else:
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)# ============================================
# API Views for Form Framework Restructure
# ============================================

def api_users_clear_all(request):
    """
    POST: Delete all users
    """
    if request.method == 'POST':
        try:
            users = _scope_users_queryset(request)
            count = users.count()
            users.delete()
            return JsonResponse({
                'success': True,
                'message': f'{count} users deleted successfully'
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    else:
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_external_tables(request):
    """
    GET: List all external tables
    POST: Create a new external table
    """
    if request.method == 'GET':
        # Return list of external tables
        tables = _scope_external_tables_queryset(request)
        data = [_serialize_external_table(table) for table in tables]
        return JsonResponse({'success': True, 'data': data})
    
    elif request.method == 'POST':
        try:
            body = json.loads(request.body)
            table_name = body.get('table_name', '')
            fields = body.get('fields', [])
            hidden_columns = _parse_json_field(body.get('hidden_columns', []), [])
            
            # Validate table name
            import re
            if not re.match(r'^[a-zA-Z0-9_]+$', table_name):
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid table name. Only letters, numbers, and underscores allowed.'
                }, status=400)
            
            # Create external table record
            table = ExternalTable.objects.create(
                table_name=table_name,
                fields_schema=fields if isinstance(fields, list) else [],
                hidden_columns=hidden_columns if isinstance(hidden_columns, list) else [],
                created_by=_get_product_owner_id(request),
            )
            
            return JsonResponse({
                'success': True,
                'message': 'External table created successfully',
                'data': _serialize_external_table(table)
            }, status=201)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    
    else:
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_external_table_detail(request, table_id):
    """
    GET: Get external table details
    DELETE: Delete external table
    """
    try:
        table = _scope_external_tables_queryset(request).get(id=table_id)
    except ObjectDoesNotExist:
        return JsonResponse({'success': False, 'error': 'Table not found'}, status=404)
    
    if request.method == 'GET':
        return JsonResponse({
            'success': True,
            'data': _serialize_external_table(table)
        })
    
    elif request.method in ('PUT', 'PATCH'):
        try:
            body = json.loads(request.body)

            if 'table_name' in body:
                new_name = str(body.get('table_name', '')).strip()
                if not new_name:
                    return JsonResponse({'success': False, 'error': 'Table name cannot be empty'}, status=400)
                tables_qs = _scope_external_tables_queryset(request).exclude(id=table.id)
                if tables_qs.filter(table_name__iexact=new_name).exists():
                    return JsonResponse({'success': False, 'error': 'Another table with this name already exists'}, status=400)
                table.table_name = new_name

            if 'fields_schema' in body:
                fields_schema = body.get('fields_schema', [])
                # JSONField handles serialization automatically - pass as list
                if isinstance(fields_schema, list):
                    table.fields_schema = fields_schema
                elif isinstance(fields_schema, str):
                    try:
                        table.fields_schema = json.loads(fields_schema)
                    except:
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
            return JsonResponse({'success': True, 'message': 'External table updated successfully', 'data': _serialize_external_table(table)})
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)

    elif request.method == 'DELETE':
        table.delete()
        return JsonResponse({'success': True, 'message': 'External table deleted successfully'})
    
    else:
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_external_table_records(request, table_id):
    """
    GET: List records in external table
    POST: Add new record to external table
    """
    try:
        table = _scope_external_tables_queryset(request).get(id=table_id)
    except ObjectDoesNotExist:
        logger.warning(f"Table not found: {table_id}")
        return JsonResponse({'success': False, 'error': 'Table not found'}, status=404)
    
    if request.method == 'GET':
        records = ExternalTableRecord.objects.filter(table=table)
        schema = table.get_fields_list() if hasattr(table, 'get_fields_list') else []
        
        normalized_records = []
        for record in records:
            serialized = _serialize_external_record(record)
            # Normalize against current schema
            serialized['data'] = normalize_record(schema, serialized.get('data', {}))
            normalized_records.append(serialized)
        
        return JsonResponse({'success': True, 'data': normalized_records})
    
    elif request.method == 'POST':
        try:
            body = json.loads(request.body)
            record_data = body.get('data', {})
            
            # Ensure data is a dict (JSONField handles serialization)
            if not isinstance(record_data, dict):
                record_data = {}
            
            record = ExternalTableRecord.objects.create(
                table=table,
                data=record_data
            )
            
            _sync_external_table_record_count(table)
            
            return JsonResponse({
                'success': True,
                'message': 'Record added successfully',
                'data': _serialize_external_record(record)
            }, status=201)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    
    else:
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_external_table_record_detail(request, table_id, record_id):
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

    elif request.method in ('PUT', 'PATCH'):
        try:
            body = json.loads(request.body)
            current_data = _serialize_external_record(record)['record_data']
            incoming_data = body.get('data')
            if isinstance(incoming_data, dict):
                current_data.update(incoming_data)

            for key in ('status', 'is_approved', 'is_rejected', 'notes'):
                if key in body:
                    current_data[key] = body.get(key)

            record.data = json.dumps(current_data)
            record.save()
            _sync_external_table_record_count(table)
            return JsonResponse({'success': True, 'message': 'Record updated successfully', 'data': _serialize_external_record(record)})
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)

    elif request.method == 'DELETE':
        record.delete()
        _sync_external_table_record_count(table)
        return JsonResponse({'success': True, 'message': 'Record deleted successfully'})

    return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_validate_registration(request):
    """
    POST: Validate registration number against main User database
    """
    if request.method == 'POST':
        try:
            body = json.loads(request.body)
            registration_number = body.get('registration_number', '').strip()
            
            if not registration_number:
                return JsonResponse({
                    'success': False,
                    'error': 'Registration number is required'
                }, status=400)
            
            # Check if user exists with this registration number
            try:
                user = _scope_users_queryset(request).get(registration_number=registration_number)
                return JsonResponse({
                    'success': True,
                    'valid': True,
                    'user': {
                        'id': user.id,
                        'full_name': user.full_name,
                        'email': user.email,
                        'registration_number': user.registration_number
                    }
                })
            except ObjectDoesNotExist:
                return JsonResponse({
                    'success': True,
                    'valid': False,
                    'error': 'Registration number not found'
                })
                
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    else:
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_external_table_signup(request):
    """
    POST: Submit member signup for external table
    """
    if request.method == 'POST':
        try:
            body = json.loads(request.body)
            table_id = body.get('table_id')
            registration_number = body.get('registration_number', '').strip()
            name = body.get('name', '').strip()
            email = body.get('email', '').strip()
            phone = body.get('phone', '').strip()
            notes = body.get('notes', '').strip()
            
            # Validate required fields
            if not all([table_id, registration_number, name, email]):
                return JsonResponse({
                    'success': False,
                    'error': 'All required fields must be provided'
                }, status=400)
            
            # Get the table
            try:
                table = _scope_external_tables_queryset(request).get(id=table_id)
            except ObjectDoesNotExist:
                return JsonResponse({
                    'success': False,
                    'error': 'Table not found'
                }, status=404)
            
            # Validate registration number exists in User table
            try:
                user = _scope_users_queryset(request).get(registration_number=registration_number)
            except ObjectDoesNotExist:
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid registration number'
                }, status=400)
            
            # Check if user is already signed up for this table
            existing_record = ExternalTableRecord.objects.filter(
                table=table,
                data__contains=f'"registration_number": "{registration_number}"'
            ).first()
            
            if existing_record:
                return JsonResponse({
                    'success': False,
                    'error': 'You have already applied for this table'
                }, status=400)
            
            # Create the signup record
            record_data = {
                'registration_number': registration_number,
                'full_name': name,
                'email': email,
                'phone': phone,
                'notes': notes,
                'user_id': user.id,
                'status': 'pending',  # pending, approved, rejected
                'submitted_at': datetime.now().isoformat()
            }
            
            # Ensure data is a dict (JSONField handles serialization)
            record_data_dict = {
                'full_name': name,
                'registration_number': registration_number,
                'phone': phone,
                'notes': notes,
                'user_id': user.id,
                'status': 'pending',
                'submitted_at': datetime.now().isoformat()
            }
            
            record = ExternalTableRecord.objects.create(
                table=table,
                data=record_data_dict
            )
            
            # Update record count
            table.record_count = ExternalTableRecord.objects.filter(table=table).count()
            table.save()
            
            return JsonResponse({
                'success': True,
                'message': 'Application submitted successfully',
                'data': {
                    'id': record.id,
                    'status': 'pending'
                }
            }, status=201)
            
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    else:
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)


def api_external_table_toggle_visibility(request, table_id):
    """
    POST: Toggle visibility of external table
    """
    try:
        table = _scope_external_tables_queryset(request).get(id=table_id)
    except ObjectDoesNotExist:
        return JsonResponse({'success': False, 'error': 'Table not found'}, status=404)
    
    if request.method == 'POST':
        try:
            body = json.loads(request.body)
            is_visible = body.get('is_visible')
            
            if is_visible is None:
                return JsonResponse({
                    'success': False,
                    'error': 'is_visible field is required'
                }, status=400)
            
            if isinstance(is_visible, str):
                table.is_visible = is_visible.lower() in ('true', '1', 'yes', 'on')
            else:
                table.is_visible = bool(is_visible)
            table.save()
            
            return JsonResponse({
                'success': True,
                'message': f'Table visibility {"enabled" if table.is_visible else "disabled"}',
                'data': {
                    'id': table.id,
                    'is_visible': table.is_visible
                }
            })
            
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    else:
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)
PUBLIC_DEMO_COUNTDOWN_KEY = "public_demo"
