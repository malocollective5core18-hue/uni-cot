"""Small permission predicates shared by founder-only source APIs."""


def is_public_founder_staff_request(request):
    """Only active staff authenticated in the public schema are founders."""
    tenant = getattr(request, 'tenant', None)
    user = getattr(request, 'user', None)
    return bool(
        getattr(tenant, 'schema_name', None) == 'public'
        and user
        and getattr(user, 'is_authenticated', False)
        and getattr(user, 'is_active', False)
        and getattr(user, 'is_staff', False)
    )
