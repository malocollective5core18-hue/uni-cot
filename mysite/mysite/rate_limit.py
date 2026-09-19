from hashlib import sha256

from django.core.cache import cache


def client_ip(request):
    """Use the left-most proxy-provided client address when configured."""
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (forwarded_for.split(",", 1)[0].strip() if forwarded_for else request.META.get("REMOTE_ADDR", "")) or "unknown"


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
