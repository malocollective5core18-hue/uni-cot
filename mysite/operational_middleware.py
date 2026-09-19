from .health import healthz, readyz
from customers.provisioning import provision_tick


class OperationalEndpointMiddleware:
    """Serve health and provisioning operations before tenant/session middleware."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/healthz/":
            return healthz(request)
        if request.path == "/readyz/":
            return readyz(request)
        if request.path == "/internal/provision-tick/":
            return provision_tick(request)
        return self.get_response(request)
