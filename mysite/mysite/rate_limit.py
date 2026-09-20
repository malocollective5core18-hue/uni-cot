from hashlib import sha256

from django.conf import settings
from django.core.cache import cache


def client_ip(request):
    """Return a forwarded client address only when the peer is trusted.

    ``X-Forwarded-For`` is supplied by the client unless the direct peer is a
    configured reverse proxy.  Never use it for a request that arrived
    directly at Django: doing so lets a caller choose a different rate-limit
    bucket for every request.
    """
    remote_addr = request.META.get("REMOTE_ADDR", "").strip()
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    trusted_proxies = set(getattr(settings, "TRUSTED_PROXY_IPS", ()))
    if remote_addr in trusted_proxies and forwarded_for:
        forwarded_addresses = [address.strip() for address in forwarded_for.split(",")]
        if forwarded_addresses and forwarded_addresses[0]:
            return forwarded_addresses[0]
    return remote_addr or "unknown"


def is_rate_limited(request, scope, *, limit, window_seconds, account=""):
    """Shared fixed-window limit; cache.add/incr is shared by Redis workers."""
    normalized_account = (account or "").strip().lower()
    raw_key = f"{scope}:{client_ip(request)}:{normalized_account}"
    cache_key = f"rate-limit:{sha256(raw_key.encode()).hexdigest()}"
    if cache.add(cache_key, 1, timeout=window_seconds):
        return False
    try:
        return cache.incr(cache_key) > limit
    except ValueError:
        cache.add(cache_key, 1, timeout=window_seconds)
        return False
