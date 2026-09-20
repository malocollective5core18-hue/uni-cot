"""Tenant-isolated realtime infrastructure.

No business view imports this module in Phase 1.  It provides the safe
transport and publishing primitives that later phases can opt into.
"""

import asyncio
import importlib
import logging
import threading
from hashlib import sha256
from time import monotonic, time

from asgiref.sync import async_to_sync, sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.layers import get_channel_layer
from django.conf import settings
from django.core.cache import cache
from django.db import connection, transaction
from django_tenants.utils import schema_context

logger = logging.getLogger(__name__)

OWNER_RESOURCES = (
    'members', 'groups', 'posts', 'properties', 'table-records',
    'slider-images', 'countdown-cards',
)
MEMBER_RESOURCES = ('posts', 'properties', 'slider-images', 'countdown-cards')
RESOURCE_FAMILIES = {
    'members': 'users',
    'groups': 'groups',
    'posts': 'posts',
    'properties': 'properties',
    'table-records': 'external_tables',
    'slider-images': 'slider_images',
    'countdown-cards': 'countdown_cards',
}

CLOSE_UNAUTHENTICATED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_TENANT_NOT_READY = 4409
CLOSE_LIMIT_REACHED = 4429

_DEBOUNCE_LOCK = threading.Lock()
_DEBOUNCE_TIMERS = {}


def channel_group_name(tenant_key, resource):
    if resource not in RESOURCE_FAMILIES:
        raise ValueError('Unknown realtime resource')
    # Channels permits only alphanumerics, hyphens, underscores, and periods
    # in a physical group name. Dots preserve the required three components;
    # colons are only used in the human-readable logical notation in docs.
    return f't.{tenant_key}.{resource}'


def _set_public_schema():
    setter = getattr(connection, 'set_schema_to_public', None)
    if callable(setter):
        setter()


def resolve_ready_path_tenant(tenant_slug, tenant_id, tenant_key):
    """Resolve only the exact ready and active path tuple in public schema."""
    from customers.models import CRTenant

    _set_public_schema()
    try:
        tenant = CRTenant.objects.filter(
            id=tenant_id,
            subdomain=tenant_slug,
            tenant_key=tenant_key,
            is_active=True,
        ).select_related('owner').first()
        if tenant is None:
            return None, 'unknown'
        if tenant.provisioning_state != CRTenant.PROVISIONING_READY:
            return None, 'not_ready'
        if not tenant.is_subscription_active:
            return None, 'forbidden'
        return tenant, None
    finally:
        _set_public_schema()


def _validate_service_session(tenant, session):
    """Validate the custom session inside the only resolved tenant schema."""
    from service.models import Member, OwnerUser

    service_user = session.get('service_user') or {}
    role = service_user.get('user_type')
    if role == 'owner':
        owner_id = service_user.get('owner_id')
        if not owner_id or owner_id != tenant.owner_id:
            return None, 'forbidden'
        _set_public_schema()
        try:
            if not OwnerUser.objects.filter(id=owner_id, is_active=True).exists():
                return None, 'forbidden'
        finally:
            _set_public_schema()
        return ('owner', owner_id), None

    if role == 'member':
        if service_user.get('tenant_id') != tenant.id or service_user.get('tenant_key') != tenant.tenant_key:
            return None, 'forbidden'
        member_id = service_user.get('member_id')
        if not member_id:
            return None, 'unauthenticated'
        try:
            with schema_context(tenant.schema_name):
                if not Member.objects.filter(id=member_id, is_active=True).exists():
                    return None, 'forbidden'
        finally:
            _set_public_schema()
        return ('member', member_id), None

    return None, 'unauthenticated'


def validate_realtime_access(tenant_slug, tenant_id, tenant_key, session):
    tenant, failure = resolve_ready_path_tenant(tenant_slug, tenant_id, tenant_key)
    if failure:
        return None, None, failure
    identity, failure = _validate_service_session(tenant, session)
    if failure:
        return None, None, failure
    return tenant, identity, None


def validate_realtime_access_for_session(tenant_slug, tenant_id, tenant_key, session_key):
    """Reload session storage so logout/expiry closes an existing socket."""
    session_engine = importlib.import_module(settings.SESSION_ENGINE)
    session = session_engine.SessionStore(session_key=session_key)
    return validate_realtime_access(tenant_slug, tenant_id, tenant_key, session.load())


def _counter_key(kind, value):
    return f'realtime:{kind}:{sha256(str(value).encode()).hexdigest()}'


def _increment_counter(key, limit, timeout):
    if cache.add(key, 1, timeout=timeout):
        return True
    try:
        count = cache.incr(key)
    except ValueError:
        return False
    if count <= limit:
        return True
    try:
        cache.decr(key)
    except ValueError:
        pass
    return False


def _decrement_counter(key):
    try:
        if cache.get(key):
            cache.decr(key)
    except ValueError:
        pass


def reserve_connection(identity, client_address):
    """Reserve bounded global/per-user capacity and rate-limit attempts."""
    attempt_key = _counter_key('attempt', client_address)
    if not _increment_counter(
        attempt_key,
        settings.REALTIME_CONNECTION_LIMIT,
        settings.REALTIME_CONNECTION_WINDOW_SECONDS,
    ):
        return None

    total_key = 'realtime:connections:total'
    if not _increment_counter(total_key, settings.REALTIME_MAX_SOCKETS_TOTAL, settings.REALTIME_IDLE_SECONDS):
        return None
    user_key = _counter_key('connections:user', identity)
    if not _increment_counter(user_key, settings.REALTIME_MAX_SOCKETS_PER_USER, settings.REALTIME_IDLE_SECONDS):
        _decrement_counter(total_key)
        return None
    return total_key, user_key


def release_connection(reservation):
    if not reservation:
        return
    total_key, user_key = reservation
    _decrement_counter(total_key)
    _decrement_counter(user_key)


def _cache_scope_for_tenant(tenant):
    return f'{tenant.schema_name}:{tenant.id}:{tenant.owner_id}'


def _cache_version_key_for_tenant(tenant, family):
    return f'ring0:v1:{_cache_scope_for_tenant(tenant)}:{family}:version'


def invalidate_resource_family(request, family):
    """Advance the same version used by the HTTP ETag family and return it."""
    tenant = getattr(request, 'tenant', None)
    schema = getattr(tenant, 'schema_name', 'public') or 'public'
    tenant_id = getattr(tenant, 'id', 'public') or 'public'
    owner_id = getattr(tenant, 'owner_id', None) or 'public'
    key = f'ring0:v1:{schema}:{tenant_id}:{owner_id}:{family}:version'
    version = time()
    cache.set(key, version, None)
    return version


def _publish_debounced(tenant_key, resource):
    try:
        tenant, failure = resolve_ready_path_tenant_by_key(tenant_key)
        if failure:
            return
        family = RESOURCE_FAMILIES[resource]
        version = cache.get(_cache_version_key_for_tenant(tenant, family), 1)
        async_to_sync(get_channel_layer().group_send)(
            channel_group_name(tenant_key, resource),
            {'type': 'realtime.changed', 'v': 1, 'resource': resource, 'version': version},
        )
    except Exception:
        logger.exception('Realtime publish failed for tenant resource change.')
    finally:
        with _DEBOUNCE_LOCK:
            _DEBOUNCE_TIMERS.pop((tenant_key, resource), None)


def resolve_ready_path_tenant_by_key(tenant_key):
    from customers.models import CRTenant

    _set_public_schema()
    try:
        tenant = CRTenant.objects.filter(tenant_key=tenant_key, is_active=True).first()
        if tenant is None or tenant.provisioning_state != CRTenant.PROVISIONING_READY:
            return None, 'not_ready'
        if not tenant.is_subscription_active:
            return None, 'forbidden'
        return tenant, None
    finally:
        _set_public_schema()


def publish_changed(tenant_key, resource):
    """Schedule a minimal post-commit notification; errors never affect writes."""
    if resource not in RESOURCE_FAMILIES:
        raise ValueError('Unknown realtime resource')

    def enqueue():
        with _DEBOUNCE_LOCK:
            key = (tenant_key, resource)
            if key in _DEBOUNCE_TIMERS:
                return
            timer = threading.Timer(0.3, _publish_debounced, args=(tenant_key, resource))
            timer.daemon = True
            _DEBOUNCE_TIMERS[key] = timer
            timer.start()

    transaction.on_commit(enqueue)


async def _async_sync(func, *args):
    return await sync_to_async(func, thread_sensitive=True)(*args)


class RealtimeConsumer(AsyncJsonWebsocketConsumer):
    """A server-assigned, tenant-scoped notification-only WebSocket."""

    async def connect(self):
        kwargs = self.scope.get('url_route', {}).get('kwargs', {})
        tenant, identity, failure = await database_sync_to_async(validate_realtime_access)(
            kwargs.get('tenant_slug'), kwargs.get('tenant_id'), kwargs.get('tenant_key'), self.scope['session']
        )
        if failure:
            await self.close(code={
                'unauthenticated': CLOSE_UNAUTHENTICATED,
                'not_ready': CLOSE_TENANT_NOT_READY,
                'unknown': CLOSE_TENANT_NOT_READY,
            }.get(failure, CLOSE_FORBIDDEN))
            return

        client_address = (self.scope.get('client') or ('unknown',))[0]
        self.reservation = await _async_sync(reserve_connection, identity, client_address)
        if not self.reservation:
            await self.close(code=CLOSE_LIMIT_REACHED)
            return

        self.tenant_path = (kwargs['tenant_slug'], kwargs['tenant_id'], kwargs['tenant_key'])
        self.identity = identity
        self.resources = OWNER_RESOURCES if identity[0] == 'owner' else MEMBER_RESOURCES
        self.groups = [channel_group_name(tenant.tenant_key, resource) for resource in self.resources]
        for group in self.groups:
            await self.channel_layer.group_add(group, self.channel_name)
        self.last_activity = monotonic()
        self.last_access_check = self.last_activity
        await self.accept()
        self.idle_task = asyncio.create_task(self._idle_watch())

    async def disconnect(self, close_code):
        task = getattr(self, 'idle_task', None)
        if task:
            task.cancel()
        for group in getattr(self, 'groups', []):
            await self.channel_layer.group_discard(group, self.channel_name)
        await _async_sync(release_connection, getattr(self, 'reservation', None))

    async def receive_json(self, content, **kwargs):
        # No subscribe protocol exists: client messages cannot choose a tenant
        # or group. Heartbeats merely prove the connection is still active.
        if content != {'type': 'heartbeat'}:
            await self.close(code=CLOSE_FORBIDDEN)
            return
        self.last_activity = monotonic()
        if self.last_activity - self.last_access_check >= settings.REALTIME_ACCESS_CACHE_SECONDS:
            tenant, identity, failure = await database_sync_to_async(validate_realtime_access_for_session)(
                *self.tenant_path, self.scope['session'].session_key
            )
            if failure or identity != self.identity:
                await self.close(code=CLOSE_FORBIDDEN)
                return
            self.last_access_check = self.last_activity

    async def realtime_changed(self, event):
        await self.send_json({
            'v': 1,
            'type': 'changed',
            'resource': event['resource'],
            'version': event['version'],
        })

    async def _idle_watch(self):
        while True:
            await asyncio.sleep(settings.REALTIME_HEARTBEAT_SECONDS)
            if monotonic() - self.last_activity > settings.REALTIME_IDLE_SECONDS:
                await self.close(code=CLOSE_FORBIDDEN)
                return
            await self.send_json({'type': 'heartbeat'})


async def _channel_layer_round_trip():
    layer = get_channel_layer()
    channel = await layer.new_channel('readyz.')
    await layer.send(channel, {'type': 'realtime.health'})
    return await layer.receive(channel)


def channel_layer_is_healthy():
    try:
        return async_to_sync(_channel_layer_round_trip)() == {'type': 'realtime.health'}
    except Exception:
        logger.exception('Realtime channel layer health check failed.')
        return False
