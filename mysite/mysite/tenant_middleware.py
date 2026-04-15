"""
Tenant Middleware for RING-0 Multi-Tenant SaaS Platform

This middleware extracts subdomain from request and sets up tenant context.
"""

import re

from django.http import JsonResponse
from django.urls import reverse
from django.shortcuts import redirect
from django.contrib import messages


class TenantMiddleware:
    """
    Middleware to detect tenant from subdomain and set schema.
    """
    
    # Public hosts that don't require tenant
    PUBLIC_HOSTS = {'localhost', '127.0.0.1', '0.0.0.0'}
    TENANT_PATH_RE = re.compile(r'^/t/(?P<tenant_slug>[^/]+)/(?P<tenant_id>\d+)(?:/|$)')
    
    def __init__(self, get_response):
        self.get_response = get_response
        
    def __call__(self, request):
        # Get the host from request
        host = request.get_host()
        hostname = host.split(':', 1)[0].lower()

        path_tenant = self._extract_path_tenant(request.path)
        if path_tenant:
            from customers.models import CRTenant

            tenant = CRTenant.objects.filter(
                id=path_tenant['tenant_id'],
                is_active=True,
            ).first()
            request.tenant = tenant

            if tenant and not tenant.is_subscription_active:
                if not request.path.startswith('/renew/') and not request.path.startswith('/admin/'):
                    messages.warning(request, 'Your subscription has expired. Please renew to continue.')
                    return redirect('service:subscription_expired', tenant_slug=tenant.subdomain, tenant_id=tenant.id)

            return self.get_response(request)
        
        # Skip tenant detection for public hosts
        if hostname in self.PUBLIC_HOSTS:
            request.tenant = None
            return self.get_response(request)
        
        # Extract subdomain
        subdomain = self._extract_subdomain(hostname)
        
        if subdomain:
            # Try to find tenant by subdomain
            from customers.models import CRTenant, Domain
            
            tenant = None
            
            # First try direct subdomain match
            try:
                tenant = CRTenant.objects.get(subdomain=subdomain, is_active=True)
            except CRTenant.DoesNotExist:
                # Try domain match
                try:
                    domain = Domain.objects.get(domain=hostname, tenant__is_active=True)
                    tenant = domain.tenant
                except Domain.DoesNotExist:
                    pass
            
            if tenant:
                # Set tenant on request for views to use
                request.tenant = tenant

                # Always route tenant root to system home
                if request.path == '/' and getattr(tenant, 'schema_name', None) != 'public':
                    return redirect('/system/')
                
                # Check subscription status
                if not tenant.is_subscription_active:
                    # Subscription expired - redirect to renewal
                    if not request.path.startswith('/renew/') and not request.path.startswith('/admin/'):
                        messages.warning(request, 'Your subscription has expired. Please renew to continue.')
                        return redirect('service:subscription_expired')
            else:
                # Tenant not found
                request.tenant = None
        else:
            request.tenant = None
        
        return self.get_response(request)
    
    def _extract_subdomain(self, host):
        """Extract subdomain from host."""
        hostname = host.split(':', 1)[0].lower()
        parts = hostname.split('.')
        
        # Handle local development: bict.localhost:8000
        if hostname.endswith('.localhost'):
            if len(parts) >= 2 and parts[0] != 'www':
                return parts[0]

        if hostname.endswith('.127.0.0.1'):
            if len(parts) >= 4 and parts[0] != 'www':
                return parts[0]
        
        # Handle production: bict.ring0.com
        if hostname.endswith('.onrender.com'):
            return None

        if len(parts) >= 3:
            # Assume first part is subdomain (unless www)
            if parts[0] != 'www':
                return parts[0]
        
        return None

    def _extract_path_tenant(self, path):
        match = self.TENANT_PATH_RE.match(path or '')
        if not match:
            return None
        return {
            'tenant_slug': match.group('tenant_slug'),
            'tenant_id': int(match.group('tenant_id')),
        }


class PublicSchemaMiddleware:
    """
    Ensure public schema (shared) models are accessible.
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        
    def __call__(self, request):
        # Tenant-specific code goes here
        # For public URLs, we might want to skip tenant-specific handling
        
        return self.get_response(request)


class TenantContextProcessor:
    """
    Add tenant context to all templates.
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        
    def __call__(self, request):
        response = self.get_response(request)
        return response
    
    def process_template_context(self, context):
        """Add tenant info to template context"""
        tenant = getattr(request, 'tenant', None)
        
        if tenant:
            context.update({
                'tenant': tenant,
                'tenant_name': tenant.name,
                'tenant_subdomain': tenant.subdomain,
                'tenant_domain': tenant.domain_url,
                'tenant_is_active': tenant.is_subscription_active,
                'tenant_days_remaining': tenant.days_remaining,
            })
        
        return context


def get_current_tenant(request):
    """Utility function to get current tenant from request"""
    return getattr(request, 'tenant', None)


def require_tenant(view_func):
    """Decorator to require valid tenant"""
    def wrapper(request, *args, **kwargs):
        tenant = getattr(request, 'tenant', None)
        
        if not tenant:
            return JsonResponse({'error': 'Tenant not found'}, status=404)
        
        if not tenant.is_active:
            return JsonResponse({'error': 'Tenant is not active'}, status=403)
        
        if not tenant.is_subscription_active:
            return JsonResponse({'error': 'Subscription expired'}, status=403)
        
        return view_func(request, *args, **kwargs)
    
    return wrapper
