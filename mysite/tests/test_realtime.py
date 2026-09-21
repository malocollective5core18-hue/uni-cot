import asyncio
import json
import os
import runpy
from unittest.mock import Mock, patch

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from asgiref.testing import ApplicationCommunicator
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.db import transaction
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings

from customers.models import CRTenant
from mysite.asgi import application
from mysite.realtime import (
    CLOSE_FORBIDDEN,
    CLOSE_LIMIT_REACHED,
    CLOSE_TENANT_NOT_READY,
    CLOSE_UNAUTHENTICATED,
    channel_group_name,
    invalidate_resource_family,
    publish_changed,
    reserve_public_connection,
    release_public_connection,
)
from service.models import OwnerUser


class RealtimeCommunicator:
    """Small offline websocket harness that avoids adding Daphne to production."""

    def __init__(self, application, path, headers):
        self.communicator = ApplicationCommunicator(application, {
            'type': 'websocket', 'path': path, 'headers': headers,
            'query_string': b'', 'client': ('127.0.0.1', 50000),
        })

    async def connect(self):
        await self.communicator.send_input({'type': 'websocket.connect'})
        response = await self.communicator.receive_output(timeout=1)
        return response['type'] == 'websocket.accept', response.get('code')

    async def send_json_to(self, content):
        await self.communicator.send_input({'type': 'websocket.receive', 'text': json.dumps(content)})

    async def receive_json_from(self):
        response = await self.communicator.receive_output(timeout=1)
        return json.loads(response['text'])

    async def receive_nothing(self, timeout):
        return await self.communicator.receive_nothing(timeout=timeout)

    async def disconnect(self):
        await self.communicator.send_input({'type': 'websocket.disconnect', 'code': 1000})
        await self.communicator.wait(timeout=1)

    async def wait(self):
        await self.communicator.wait(timeout=1)


class RealtimeWebSocketTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.owner = OwnerUser.objects.create(
            email='realtime-owner@example.com', program_name='Realtime',
            password=make_password('secret123'), is_owner=True, is_active=True,
        )
        self.tenant = CRTenant.objects.create(
            name='Realtime', schema_name='realtime_socket', subdomain='realtime',
            tenant_key='realtimekey123456789', owner=self.owner, is_active=True,
            provisioning_state=CRTenant.PROVISIONING_READY,
        )

    def _path(self, tenant=None):
        tenant = tenant or self.tenant
        return f'/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/ws/realtime/'

    def _cookie(self, owner=None, tenant=None):
        owner = owner or self.owner
        tenant = tenant or self.tenant
        session = self.client.session
        session['service_user'] = {'user_type': 'owner', 'owner_id': owner.id}
        session['tenant_id'] = tenant.id
        session['tenant_key'] = tenant.tenant_key
        session.save()
        return f'ring0_session={session.session_key}'.encode()

    async def _connect(self, path, cookie=None, origin=b'http://testserver'):
        headers = [(b'origin', origin)]
        if cookie:
            headers.append((b'cookie', cookie))
        communicator = RealtimeCommunicator(application, path, headers=headers)
        connected, detail = await communicator.connect()
        return communicator, connected, detail

    def test_owner_receives_only_its_tenant_minimal_group_event(self):
        async def scenario():
            communicator, connected, _ = await self._connect(self._path(), self._cookie())
            self.assertTrue(connected)
            await get_channel_layer().group_send(
                channel_group_name(self.tenant.tenant_key, 'groups'),
                {'type': 'realtime.changed', 'v': 1, 'resource': 'groups', 'version': 7, 'email': 'never-send'},
            )
            payload = await communicator.receive_json_from()
            self.assertEqual(payload, {'v': 1, 'type': 'changed', 'resource': 'groups', 'version': 7})
            await communicator.disconnect()

        async_to_sync(scenario)()

    def test_cross_tenant_owner_never_receives_other_tenant_event(self):
        other_owner = OwnerUser.objects.create(
            email='realtime-other@example.com', program_name='Other', password='hash', is_owner=True, is_active=True,
        )
        other = CRTenant.objects.create(
            name='Other', schema_name='realtime_other', subdomain='other',
            tenant_key='otherkey123456789012', owner=other_owner, is_active=True,
            provisioning_state=CRTenant.PROVISIONING_READY,
        )

        async def scenario():
            communicator, connected, _ = await self._connect(self._path(), self._cookie())
            self.assertTrue(connected)
            await get_channel_layer().group_send(
                channel_group_name(other.tenant_key, 'groups'),
                {'type': 'realtime.changed', 'v': 1, 'resource': 'groups', 'version': 8},
            )
            self.assertTrue(await communicator.receive_nothing(timeout=0.1))
            await communicator.disconnect()

        async_to_sync(scenario)()

    def test_rejects_anonymous_forged_and_unready_tenants(self):
        async def scenario():
            anonymous, connected, code = await self._connect(self._path())
            self.assertFalse(connected)
            self.assertEqual(code, CLOSE_UNAUTHENTICATED)

            forged, connected, code = await self._connect(
                f'/t/{self.tenant.subdomain}/{self.tenant.id}/wrongwrongwrongwrong12/ws/realtime/', self._cookie()
            )
            self.assertFalse(connected)
            self.assertEqual(code, CLOSE_TENANT_NOT_READY)

        async_to_sync(scenario)()

    def test_rejects_pending_tenant(self):
        self.tenant.provisioning_state = CRTenant.PROVISIONING_PENDING
        self.tenant.save(update_fields=['provisioning_state'])

        async def scenario():
            pending, connected, code = await self._connect(self._path(), self._cookie())
            self.assertFalse(connected)
            self.assertEqual(code, CLOSE_TENANT_NOT_READY)

        async_to_sync(scenario)()

    def test_rejects_every_non_ready_tenant_state_and_stale_owner(self):
        for state in (
            CRTenant.PROVISIONING_PENDING,
            CRTenant.PROVISIONING_IN_PROGRESS,
            CRTenant.PROVISIONING_FAILED,
        ):
            self.tenant.provisioning_state = state
            self.tenant.save(update_fields=['provisioning_state'])

            async def scenario():
                denied, connected, code = await self._connect(self._path(), self._cookie())
                self.assertFalse(connected)
                self.assertEqual(code, CLOSE_TENANT_NOT_READY)

            async_to_sync(scenario)()

        self.tenant.provisioning_state = CRTenant.PROVISIONING_READY
        self.tenant.save(update_fields=['provisioning_state'])
        self.owner.is_active = False
        self.owner.save(update_fields=['is_active'])

        async def stale_session():
            denied, connected, code = await self._connect(self._path(), self._cookie())
            self.assertFalse(connected)
            self.assertEqual(code, CLOSE_FORBIDDEN)

        async_to_sync(stale_session)()

    def test_rejects_other_tenant_session_and_client_subscribe_message(self):
        other_owner = OwnerUser.objects.create(
            email='realtime-forged@example.com', program_name='Forged', password='hash', is_owner=True, is_active=True,
        )

        async def scenario():
            denied, connected, code = await self._connect(self._path(), self._cookie(owner=other_owner))
            self.assertFalse(connected)
            self.assertEqual(code, CLOSE_FORBIDDEN)

            communicator, connected, _ = await self._connect(self._path(), self._cookie())
            self.assertTrue(connected)
            await communicator.send_json_to({'type': 'subscribe', 'tenant_id': 999})
            await communicator.wait()

        async_to_sync(scenario)()

    def test_origin_validation_and_socket_cap(self):
        async def scenario():
            invalid, connected, _ = await self._connect(self._path(), self._cookie(), b'https://evil.invalid')
            self.assertFalse(connected)

            with override_settings(REALTIME_MAX_SOCKETS_PER_USER=1):
                first, connected, _ = await self._connect(self._path(), self._cookie())
                self.assertTrue(connected)
                second, connected, code = await self._connect(self._path(), self._cookie())
                self.assertFalse(connected)
                self.assertEqual(code, CLOSE_LIMIT_REACHED)
                await first.disconnect()

        async_to_sync(scenario)()

    def test_connection_attempt_limit_rejects_before_capacity_is_exhausted(self):
        async def scenario():
            with override_settings(REALTIME_CONNECTION_LIMIT=1, REALTIME_MAX_SOCKETS_PER_USER=5):
                first, connected, _ = await self._connect(self._path(), self._cookie())
                self.assertTrue(connected)
                second, connected, code = await self._connect(self._path(), self._cookie())
                self.assertFalse(connected)
                self.assertEqual(code, CLOSE_LIMIT_REACHED)
                await first.disconnect()

        async_to_sync(scenario)()


class PublicRealtimeWebSocketTests(RealtimeWebSocketTests):
    def _public_path(self, tenant=None):
        tenant = tenant or self.tenant
        return f'/t/{tenant.subdomain}/{tenant.id}/{tenant.tenant_key}/ws/public/'

    async def _connect_public(self, path, origin=b'http://testserver'):
        communicator = RealtimeCommunicator(application, path, headers=[
            (b'origin', origin), (b'host', b'testserver'),
        ])
        connected, detail = await communicator.connect()
        return communicator, connected, detail

    def test_public_flag_off_rejects_without_capacity_or_polling_change(self):
        async def scenario():
            with override_settings(REALTIME_PUBLIC_ENABLED=False):
                socket, connected, code = await self._connect_public(self._public_path())
                self.assertFalse(connected)
                self.assertEqual(code, CLOSE_FORBIDDEN)
        async_to_sync(scenario)()

    def test_public_capacity_counters_release_on_disconnect(self):
        with override_settings(
            REALTIME_PUBLIC_MAX_SOCKETS_TOTAL=1,
            REALTIME_PUBLIC_MAX_SOCKETS_PER_IP=1,
            REALTIME_PUBLIC_MAX_SOCKETS_PER_TENANT=1,
        ):
            reservation = reserve_public_connection(self.tenant.tenant_key, '198.51.100.10')
            self.assertIsNotNone(reservation)
            self.assertIsNone(reserve_public_connection(self.tenant.tenant_key, '198.51.100.10'))
            release_public_connection(reservation)
            replacement = reserve_public_connection(self.tenant.tenant_key, '198.51.100.10')
            self.assertIsNotNone(replacement)
            release_public_connection(replacement)

    def test_public_socket_receives_allowlisted_minimal_event(self):
        async def scenario():
            with override_settings(REALTIME_PUBLIC_ENABLED=True):
                socket, connected, _ = await self._connect_public(self._public_path())
                self.assertTrue(connected)
                await get_channel_layer().group_send(
                    f't.{self.tenant.tenant_key}.public.slider-images',
                    {'type': 'realtime.changed', 'v': 1, 'resource': 'slider-images', 'version': 9, 'secret': 'no'},
                )
                self.assertEqual(await socket.receive_json_from(), {
                    'v': 1, 'type': 'changed', 'resource': 'slider-images', 'version': 9,
                })
                await socket.disconnect()
        async_to_sync(scenario)()

    def test_public_socket_rejects_bad_origin_and_owner_resources(self):
        async def scenario():
            with override_settings(REALTIME_PUBLIC_ENABLED=True):
                socket, connected, code = await self._connect_public(self._public_path(), b'https://evil.invalid')
                self.assertFalse(connected)
                socket, connected, _ = await self._connect_public(self._public_path())
                self.assertTrue(connected)
                await get_channel_layer().group_send(
                    f't.{self.tenant.tenant_key}.public.users',
                    {'type': 'realtime.changed', 'resource': 'users', 'version': 2},
                )
                self.assertTrue(await socket.receive_nothing(timeout=0.1))
                await socket.disconnect()
        async_to_sync(scenario)()


class RealtimePublishingTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_invalidation_returns_the_etag_version_without_querying_database(self):
        request = RequestFactory().get('/api/groups/')
        request.tenant = type('Tenant', (), {'schema_name': 'tenant', 'id': 4, 'owner_id': 8})()
        with self.assertNumQueries(0):
            version = invalidate_resource_family(request, 'groups')
        self.assertEqual(cache.get('ring0:v1:tenant:4:8:groups:version'), version)

    def test_publish_is_post_commit_coalesced_and_publish_errors_are_swallowed(self):
        with patch('mysite.realtime.threading.Timer') as timer:
            timer.return_value = Mock()
            with self.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    publish_changed('tenantkey1234567890', 'groups')
                    publish_changed('tenantkey1234567890', 'groups')
            self.assertEqual(timer.call_count, 1)

        with patch('mysite.realtime.resolve_ready_path_tenant_by_key', side_effect=RuntimeError('redis unavailable')):
            from mysite import realtime

            realtime._publish_debounced('tenantkey1234567890', 'groups')

    def test_readyz_checks_the_configured_channel_layer(self):
        with patch.dict(os.environ, {'REDIS_URL': 'redis://test-only'}, clear=False):
            response = self.client.get('/readyz/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['checks']['channel_layer'], 'ok')


class ProvisioningRunnerTests(TestCase):
    def test_gunicorn_hook_starts_runner_only_when_explicitly_enabled(self):
        config = runpy.run_path('gunicorn.conf.py')
        with patch.dict(os.environ, {'TENANT_PROVISION_INPROCESS': 'false'}, clear=False), \
                patch('customers.provisioning.start_provisioner') as start:
            config['post_fork'](None, None)
            start.assert_not_called()

        with patch.dict(os.environ, {'TENANT_PROVISION_INPROCESS': 'true'}, clear=False), \
                patch('customers.provisioning.start_provisioner') as start:
            config['post_fork'](None, None)
            start.assert_called_once_with()
