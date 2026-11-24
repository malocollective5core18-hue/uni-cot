import json
import re
import tempfile
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection
from django_tenants.utils import schema_context
from django.contrib.auth.hashers import check_password, make_password
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.template.loader import render_to_string
from django.urls import reverse

from core import views
from core.models import ExternalTable, ExternalTableRecord, FounderTimetableSource, Property, TenantTimetableState, User, UserGroup, UserGroupMember
from customers.models import CRTenant, TenantDashboardMetric
from service.models import OwnerUser
from mysite.mysite.rate_limit import client_ip, is_rate_limited
from mysite.tests.helpers import CacheIsolationMixin


class SessionConfigurationTests(SimpleTestCase):
    def test_sessions_are_cache_backed_and_not_saved_on_every_request(self):
        self.assertEqual(settings.SESSION_ENGINE, 'django.contrib.sessions.backends.cache')
        self.assertEqual(settings.SESSION_CACHE_ALIAS, 'default')
        self.assertFalse(settings.SESSION_SAVE_EVERY_REQUEST)
        self.assertEqual(settings.SESSION_COOKIE_AGE, 60 * 60 * 24 * 7)


@override_settings(
    STORAGES={
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    },
)
class CompanyShowcaseTemplateTests(TestCase):
    """The public showcase is static and must not add render-time queries."""

    external_urls = (
        "https://lomi-apple.onrender.com",
        "https://speakora-ghfw.onrender.com/",
        "https://stanslau2025.github.io/DFIX_DGXcore",
        "https://34.35.47.111:3000",
        "https://www.mauzosheetai.co.tz/",
        "https://zima.co.tz/",
    )

    def _assert_showcase(self, template_name):
        with self.assertNumQueries(0):
            rendered = render_to_string(template_name, {
                "comments": [],
                "review_summary": {},
                "tenant_base_path": "",
            })

        vision_position = rendered.index('id="vision-projects"')
        collaborations_position = rendered.index('id="collaborations"')
        blog_position = rendered.index('id="blog"')
        self.assertLess(vision_position, collaborations_position)
        self.assertLess(collaborations_position, blog_position)

        for url in self.external_urls:
            self.assertRegex(
                rendered,
                rf'href="{re.escape(url)}"[^>]*target="_blank"[^>]*rel="noopener noreferrer"',
            )
        self.assertIn('BLACKSCIENCE Technologies', rendered)

    def test_index_showcase_renders_before_blog_without_queries(self):
        self._assert_showcase("index.html")

    def test_welcome_showcase_renders_before_blog_without_queries(self):
        self._assert_showcase("welcome.html")


class TenantLivePollingTemplateTests(SimpleTestCase):
    def test_venue_picker_can_search_filter_and_prioritize_current_cohort(self):
        source = (settings.BASE_DIR / 'templates' / 'timetable.html').read_text()
        self.assertIn('id="vfVenueSearchToggle"', source)
        self.assertIn('id="vfVenueSearch"', source)
        self.assertIn('id="vfVenueStatusFilter"', source)
        self.assertIn('<option value="no-period">No kipindi</option>', source)
        self.assertIn('<option value="hakipo">Free - HAKIPO</option>', source)
        self.assertIn('recommendedForCurrentClass: ownClassHakipo', source)
        self.assertIn('priority: ownClassHakipo ? 0 : hakipo ? 1 : 2', source)
        self.assertIn('a.priority - b.priority', source)

    def test_schedule_management_iframe_waits_for_owner_gate(self):
        source = (settings.BASE_DIR / 'templates' / 'system_index.html').read_text()
        section = source[source.index('id="scheduleManagementSection"'):source.index('<!-- CARDS MANAGEMENT SECTION -->')]
        self.assertIn('id="scheduleManagementFrame"', section)
        self.assertIn('data-src=', section)
        self.assertIn('?demo=1', section)
        self.assertNotIn('setup=1', section)
        self.assertIn("tenant.schema_name != 'public'", section)
        self.assertNotRegex(section, r'<iframe[^>]+\ssrc=')
        self.assertIn("event.source !== frame.contentWindow", source)
        self.assertIn("uni-cot-timetable-frame-height", source)
        self.assertIn('Math.abs(frame.getBoundingClientRect().height - nextHeight) < 2', source)

        opener = source[source.index('async function openScheduleManagement()'):source.index('async function openSystemSettings()')]
        self.assertLess(opener.index("ensureOwnerAdminSectionAccess('Schedule and Venue Management')"), opener.index('frame.src = frameUrl.toString()'))

    def test_timetable_page_allows_same_origin_popup_embedding(self):
        request = RequestFactory().get('/timetable/')
        queries = []
        def record_query(execute, sql, params, many, context):
            queries.append(sql)
            return execute(sql, params, many, context)
        with connection.execute_wrapper(record_query):
            response = views.timetable(request)
        self.assertEqual(queries, [])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')
        rendered = response.content.decode()
        self.assertNotIn('class="topbar"', rendered)
        self.assertNotIn('id="cohortSearch"', rendered)
        self.assertNotIn('id="classSelect"', rendered)
        self.assertNotIn('Read-only timetable.', rendered)
        self.assertIn(
            'body.timetable-reader table.timetable tbody tr { height: clamp(38px, 6vh, 46px) !important; }',
            rendered,
        )
        self.assertIn('overflow-y: auto', rendered)
        self.assertIn('function reportTimetableFrameHeight()', rendered)
        self.assertIn('id="printTimetableBtn"', rendered)
        self.assertIn('id="cohortHeading">BAE (54) • Week 1</div>', rendered)
        self.assertIn('aria-label="Download timetable as PDF"', rendered)
        self.assertIn('⬇ Download PDF</button>', rendered)
        self.assertIn("resource: 'timetable-state'", rendered)
        self.assertIn('minMs: CAN_MANAGE ? 30000 : 5000', rendered)
        self.assertIn('examDownloadPanel', rendered)
        self.assertIn('data-exam-download="test1"', rendered)
        self.assertIn('exam_tt_seen_${examType}', rendered)
        self.assertIn("contentType.includes('application/pdf')", rendered)
        self.assertIn('expected_revision: stateRevision', rendered)
        self.assertIn('const overrides = new Map();', rendered)
        self.assertIn('overrides.get(key(item))?.shift()', rendered)
        self.assertIn('id="managerCohortNav"', rendered)
        self.assertIn("document.getElementById('cohortHeading').textContent = `${state.cohort} • ${state.week}`;", rendered)
        self.assertIn("button.setAttribute('aria-label', `Select ${cohort}`);", rendered)
        self.assertIn('button.active.has-period-badge .cohort-nav-label{display:none}', rendered)
        self.assertNotIn('button.active .cohort-nav-label{display:none}', rendered)
        self.assertIn("cohortLabel.className = 'cohort-nav-label';", rendered)
        self.assertIn('#printTimetableBtn{margin-left:auto;white-space:nowrap;height:24px;min-height:24px;padding:0 7px;font-size:.62rem;gap:3px;border-radius:6px}', rendered)
        self.assertIn('getNextPeriodForCohort', rendered)
        self.assertIn("badge.textContent = cancelled ? 'NOT HELD' : period.venue || 'Venue TBC';", rendered)
        self.assertIn('max-width:min(30vw,180px)', rendered)
        self.assertIn("badge.setAttribute('aria-label', badge.title);", rendered)
        self.assertIn('!tempHides(item)', rendered)
        self.assertIn('if (!REAL_TENANT || !window.TenantLive) return;', rendered)
        self.assertIn('NOT HELD', rendered)
        self.assertIn('@page{size:A4 landscape;margin:10mm}', rendered)
        self.assertIn('.schedule-block.is-obtained,.schedule-block.is-temporary,.schedule-block.is-hakipo,.schedule-block.conflict-venue{display:none!important}', rendered)
        self.assertIn('window.addEventListener(\'resize\', () => {', rendered)
        self.assertIn(
            '#scheduleModal { align-items: center !important; justify-content: center !important; padding: 12px !important; }',
            rendered,
        )

    def test_manager_cohort_label_is_hidden_only_when_its_period_badge_is_rendered(self):
        response = views.timetable(RequestFactory().get('/timetable/?demo=1'))
        rendered = response.content.decode()
        nav_renderer = rendered[
            rendered.index('function renderManagerCohortNav()'):rendered.index('function getNextPeriodForCohort(')
        ]
        period_block = nav_renderer[
            nav_renderer.index('if (period) {'):nav_renderer.index("button.addEventListener('click'")
        ]

        self.assertIn('button.classList.toggle(\'active\', isSelectedCohort);', nav_renderer)
        self.assertIn('if (period) {', nav_renderer)
        self.assertLess(
            period_block.index('button.appendChild(badge);'),
            period_block.index("button.classList.add('has-period-badge');"),
        )
        self.assertIn("const cancelled = period.status === 'HAKIPO';", period_block)
        self.assertIn("cancelled ? 'NOT HELD'", period_block)
        self.assertIn('nav.replaceChildren();', nav_renderer)
        self.assertIn('state.cohort = cohort;', nav_renderer)
        self.assertIn('render();', nav_renderer)

    def test_public_demo_timetable_exposes_manager_controls_and_local_saves(self):
        request = RequestFactory().get('/timetable/?demo=1')
        response = views.timetable(request)
        self.assertEqual(response.status_code, 200)
        rendered = response.content.decode()
        self.assertIn('class="timetable-manager"', rendered)
        self.assertIn('body.timetable-manager .controls-bar > .control-pill{display:none!important}', rendered)
        self.assertIn('body.timetable-manager .controls-bar{display:flex;justify-content:flex-end}', rendered)
        self.assertIn('body.timetable-manager .controls-bar > .btn{height:24px!important;min-height:24px;padding:0 7px!important;font-size:.62rem!important;', rendered)
        self.assertIn('justify-content: flex-end;', rendered)
        self.assertIn('function reportTimetableFrameHeight()', rendered)
        self.assertIn('body.timetable-manager table.timetable tbody tr{height:auto!important}', rendered)
        self.assertIn('body.timetable-manager .schedule-block{height:auto!important;', rendered)
        self.assertIn('workspace.scrollHeight', rendered)
        controls = rendered[rendered.index('<div class="controls-bar">'):rendered.index('</div>\n  </header>')]
        self.assertIn('<button class="btn btn-venue" id="openVenuePanelBtn">', controls)
        self.assertIn('<span>venue</span>', controls)
        self.assertNotIn('<div class="bottom-action-bar">', rendered)
        self.assertIn("window.parent.postMessage({ type: 'uni-cot-timetable-frame-height'", rendered)
        self.assertIn('id="institutionSubtitle"', rendered)
        self.assertIn('institutionSubtitle.textContent = formatInstitutionSubtitle(state.institutionTitle)', rendered)
        self.assertIn('.brand-title p { display: block; font-size: 0.58rem; line-height: 1.15; }', rendered)
        self.assertNotIn('.brand-title p { display: none; }', rendered)
        self.assertIn('<div class="brand">', rendered)
        self.assertIn('const DEMO_MODE = true;', rendered)
        self.assertIn('localStorage.setItem(DEMO_STORAGE_KEY', rendered)

    def test_shared_live_client_is_small_and_all_tenant_pages_include_it(self):
        live_path = settings.BASE_DIR / "static" / "js" / "tenant-live.js"
        self.assertLess(live_path.stat().st_size, 3 * 1024)
        for template_name in ("system_index.html", "groups.html", "external_tables.html", "properties.html"):
            source = (settings.BASE_DIR / "templates" / template_name).read_text()
            self.assertIn("js/tenant-live.js", source)

    def test_tenant_pages_register_only_recursive_live_resources(self):
        system = (settings.BASE_DIR / "templates" / "system_index.html").read_text()
        groups = (settings.BASE_DIR / "templates" / "groups.html").read_text()
        external = (settings.BASE_DIR / "templates" / "external_tables.html").read_text()
        properties = (settings.BASE_DIR / "templates" / "properties.html").read_text()
        for resource in ("slider-images", "countdown-cards", "members", "groups"):
            self.assertIn(f"resource: '{resource}'", system)
        self.assertIn("resource: 'members'", groups)
        self.assertIn("resource: 'groups'", groups)
        self.assertIn("resource: 'table-records'", external)
        self.assertIn("resource: 'properties'", properties)
        self.assertNotIn("setInterval", external[external.index("function startExternalTablesRefreshTimer"):external.index("function renderTablesList")])
        self.assertNotIn("setInterval", properties[properties.index("function startAdminUpdatesPolling"):properties.index("function stopAdminUpdatesPolling")])


class MockTimetableStateApiTests(SimpleTestCase):
    """Mock-based API checks; these tests do not prove database behavior."""

    source = {'filename': 'founder.xlsx', 'url': '/private/founder.xlsx', 'version': 4}

    def _tenant(self):
        return SimpleNamespace(schema_name='tenant_a', owner_id=12, is_active=True)

    def _exam_sources(self):
        return {
            key: {'type': key, 'filename': None, 'version': 0, 'uploaded_at': None, 'has_file': False}
            for key in ('test1', 'test2', 'ue')
        }

    def test_mock_based_reader_put_is_rejected(self):
        """Mock-based authorization check; it does not exercise the database."""
        request = RequestFactory().put('/t/tenant/1/key/api/timetable/state/', data=b'{}')
        request.tenant = self._tenant()
        request.session = {'service_user': {'user_type': 'member', 'owner_id': 12}}
        with patch('service.views._has_owner_admin_access', return_value=False):
            response = views.api_timetable_state(request)
        self.assertEqual(response.status_code, 403)

    def test_mock_based_unauthenticated_put_is_rejected(self):
        """Mock-based unauthenticated check; it does not exercise the database."""
        request = RequestFactory().put('/t/tenant/1/key/api/timetable/state/', data=b'{}')
        request.tenant = self._tenant()
        request.session = {}
        with patch('service.views._has_owner_admin_access', return_value=False):
            response = views.api_timetable_state(request)
        self.assertEqual(response.status_code, 403)

    def test_mock_based_malformed_and_invalid_utf8_json_are_rejected(self):
        """Mock-based payload checks; they do not exercise the database."""
        with patch('service.views._has_owner_admin_access', return_value=True), patch.object(
            views, '_founder_timetable_source_payload', return_value=self.source
        ):
            for body in (b'{', b'\xff'):
                request = RequestFactory().put(
                    '/t/tenant/1/key/api/timetable/state/', data=body,
                    content_type='application/json',
                )
                request.tenant = self._tenant()
                request.session = {'service_user': {'user_type': 'owner', 'owner_id': 12}}
                with self.subTest(body=body):
                    response = views.api_timetable_state(request)
                    self.assertEqual(response.status_code, 400)
                    self.assertFalse(json.loads(response.content)['success'])

    def test_mock_based_stale_expected_revision_is_rejected(self):
        """Mock-based stale revision check; it does not prove row locking."""
        state_row = SimpleNamespace(revision=8)
        manager = Mock()
        manager.select_for_update.return_value.get_or_create.return_value = (state_row, False)
        body = json.dumps({
            'expected_revision': 7,
            'source_version': self.source['version'],
            'state': {'schedules': [{'id': 'imp-1', 'day': 'Monday', 'startSlot': 0, 'duration': 1}], 'published': False},
        })
        request = RequestFactory().put(
            '/t/tenant/1/key/api/timetable/state/', data=body,
            content_type='application/json',
        )
        request.tenant = self._tenant()
        request.session = {'service_user': {'user_type': 'owner', 'owner_id': 12}}
        with patch('service.views._has_owner_admin_access', return_value=True), patch.object(
            views, '_founder_timetable_source_payload', return_value=self.source
        ), patch.object(views.TenantTimetableState, 'objects', manager), patch.object(
            views.transaction, 'atomic', return_value=nullcontext()
        ):
            response = views.api_timetable_state(request)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(json.loads(response.content)['revision'], 8)
        manager.select_for_update.return_value.get_or_create.assert_called_once()

    def test_mock_based_reader_get_hides_founder_url_and_stale_state(self):
        """Mock-based tenant privacy check; it does not exercise schema isolation."""
        state_row = SimpleNamespace(
            state={'published': True, 'schedules': [{'id': 'imp-1'}]},
            source_version=3, revision=2,
        )
        manager = Mock()
        manager.filter.return_value.first.return_value = state_row
        request = RequestFactory().get('/t/tenant/1/key/api/timetable/state/')
        request.tenant = self._tenant()
        request.session = {'service_user': {'user_type': 'member', 'owner_id': 12}}
        with patch.object(views, 'schema_context', return_value=nullcontext()), patch.object(
            views, '_founder_timetable_source_payload', return_value=self.source
        ), patch('exams.services.exam_sources_metadata', return_value=self._exam_sources()), patch.object(
            views.TenantTimetableState, 'objects', manager
        ), patch(
            'service.views._has_owner_admin_access', return_value=False
        ):
            response = views.api_timetable_state(request)
        payload = json.loads(response.content)
        self.assertNotIn('url', payload['source'])
        self.assertEqual(payload['timetable_status'], 'owner_setup')
        self.assertIsNone(payload['data'])

    def test_mock_based_public_reader_get_hides_founder_url(self):
        """Mock-based public privacy check; it does not exercise the database."""
        request = RequestFactory().get('/api/timetable/state/')
        request.tenant = None
        request.session = {}
        with patch.object(views, 'schema_context', return_value=nullcontext()), patch.object(
            views, '_founder_timetable_source_payload', return_value=self.source
        ), patch('exams.services.exam_sources_metadata', return_value=self._exam_sources()), patch(
            'service.views._has_owner_admin_access', return_value=False
        ):
            response = views.api_timetable_state(request)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('url', json.loads(response.content)['source'])

    def test_mock_based_reader_get_returns_304_for_matching_etag(self):
        """Mock-based ETag check; it does not exercise persistence."""
        manager = Mock()
        manager.filter.return_value.first.return_value = None
        request = RequestFactory().get('/api/timetable/state/')
        request.tenant = None
        request.session = {}
        with patch.object(views, 'schema_context', return_value=nullcontext()), patch.object(
            views, '_founder_timetable_source_payload', return_value=None
        ), patch('exams.services.exam_sources_metadata', return_value=self._exam_sources()), patch.object(
            views.TenantTimetableState, 'objects', manager
        ):
            first = views.api_timetable_state(request)
            repeated_request = RequestFactory().get(
                '/api/timetable/state/', HTTP_IF_NONE_MATCH=first['ETag'],
            )
            repeated_request.tenant = None
            repeated_request.session = {}
            second = views.api_timetable_state(repeated_request)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 304)


class HealthEndpointTests(CacheIsolationMixin, TestCase):
    def test_healthz_makes_no_database_queries_or_session_cookie(self):
        with self.assertNumQueries(0):
            response = self.client.get('/healthz/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'ok')
        self.assertNotIn('sessionid', response.cookies)

    def test_readyz_checks_database_and_cache_without_session_cookie(self):
        # django-tenants activates the public schema before readyz performs
        # its single database probe.
        with self.assertNumQueries(2):
            response = self.client.get('/readyz/')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ready'])
        self.assertNotIn('sessionid', response.cookies)

    @override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "health-tests"}})
    @patch.dict('os.environ', {'REDIS_URL': 'redis://localhost:6379/0'}, clear=False)
    def test_readyz_reports_unhealthy_when_cache_probe_fails(self):
        with patch('mysite.health.cache.get', return_value=None):
            response = self.client.get('/readyz/')

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()['ready'])


class ProvisionTickEndpointTests(CacheIsolationMixin, SimpleTestCase):
    @patch.dict('os.environ', {'PROVISION_TICK_ALLOW_QUERY_TOKEN': 'false'}, clear=False)
    def test_provision_tick_is_404_when_disabled(self):
        response = self.client.get('/internal/provision-tick/?token=secret')

        self.assertEqual(response.status_code, 404)

    @patch.dict('os.environ', {'PROVISION_TICK_ALLOW_QUERY_TOKEN': 'true', 'PROVISION_TICK_TOKEN': 'secret'}, clear=False)
    @patch('customers.provisioning.start_provisioner')
    @patch('customers.provisioning.kick_provisioner')
    def test_provision_tick_accepts_valid_token(self, kick_mock, start_mock):
        response = self.client.get('/internal/provision-tick/?token=secret')

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.json()['accepted'])
        start_mock.assert_called_once()
        kick_mock.assert_called_once()

    @patch.dict('os.environ', {'PROVISION_TICK_ALLOW_QUERY_TOKEN': 'true', 'PROVISION_TICK_TOKEN': 'secret'}, clear=False)
    def test_provision_tick_rejects_invalid_token(self):
        response = self.client.get('/internal/provision-tick/?token=wrong')

        self.assertEqual(response.status_code, 401)


class CachedJsonResponseTests(SimpleTestCase):
    def test_matching_etag_returns_not_modified_without_building_payload(self):
        factory = RequestFactory()
        first = views._cached_json_response(
            factory.get('/api/properties/'), 'etag-test', lambda: {'success': True}
        )
        etag = first['ETag']
        response = views._cached_json_response(
            factory.get('/api/properties/', HTTP_IF_NONE_MATCH=etag),
            'etag-test',
            lambda: self.fail('payload builder must not run for a matching ETag'),
        )

        self.assertEqual(response.status_code, 304)
        self.assertEqual(response['ETag'], etag)
        cache.clear()


class PaginationTests(SimpleTestCase):
    def test_page_size_is_capped_at_one_hundred(self):
        request = RequestFactory().get('/api/users/?page_size=1000')

        page, meta = views._paginate_queryset(request, list(range(101)))

        self.assertEqual(len(page.object_list), 100)
        self.assertEqual(meta['page_size'], 100)
        self.assertEqual(meta['total_pages'], 2)


class ApiErrorResponseTests(SimpleTestCase):
    def test_unexpected_api_error_does_not_include_exception_details(self):
        response = views._unexpected_api_error()

        self.assertEqual(response.status_code, 500)
        self.assertJSONEqual(
            response.content,
            {'success': False, 'error': 'Unable to complete this request. Please try again later.'},
        )


class SharedRateLimitTests(CacheIsolationMixin, SimpleTestCase):
    def test_limit_is_not_stored_in_the_session(self):
        request = RequestFactory().post('/', REMOTE_ADDR='203.0.113.10')

        self.assertFalse(is_rate_limited(request, 'test', limit=2, window_seconds=60, account='User@Example.com'))
        self.assertFalse(is_rate_limited(request, 'test', limit=2, window_seconds=60, account='user@example.com'))
        self.assertTrue(is_rate_limited(request, 'test', limit=2, window_seconds=60, account='user@example.com'))
        self.assertFalse(hasattr(request, 'session'))

    @override_settings(TRUSTED_PROXY_IPS=('127.0.0.1',))
    def test_client_ip_uses_forwarded_address_only_from_trusted_proxy(self):
        proxied_request = RequestFactory().get(
            '/',
            REMOTE_ADDR='127.0.0.1',
            HTTP_X_FORWARDED_FOR='198.51.100.23, 127.0.0.1',
        )
        direct_request = RequestFactory().get(
            '/',
            REMOTE_ADDR='198.51.100.99',
            HTTP_X_FORWARDED_FOR='198.51.100.23',
        )

        self.assertEqual(client_ip(proxied_request), '198.51.100.23')
        self.assertEqual(client_ip(direct_request), '198.51.100.99')


class MemberPollingTemplateTests(SimpleTestCase):
    def test_member_pollers_are_tenant_safe_and_back_off_on_access_errors(self):
        groups_source = (settings.BASE_DIR / 'templates/groups.html').read_text()
        system_source = (settings.BASE_DIR / 'templates/system_index.html').read_text()

        self.assertIn('const API_BASE_URL = TENANT_BASE_PATH ?', groups_source)
        self.assertIn('function handleMemberPollingError(error)', groups_source)
        self.assertIn('if (![401, 403, 429].includes(status)) return true;', groups_source)
        self.assertIn('Too many requests, retrying shortly.', groups_source)
        self.assertIn('const delay = Math.max(30000, (error.retryAfter || 30) * 1000);', groups_source)
        self.assertIn("window.TenantLive.refreshNow('members');", groups_source)

        self.assertIn('const FAST_REFRESH_MS = 30000;', system_source)
        self.assertIn('usersPollingDisabled = true', system_source)
        self.assertIn('error.retryAfter', system_source)
        self.assertIn('window.setTimeout(pollUsers, usersPollingDelay)', system_source)

    def test_system_startup_does_not_request_owner_directory_for_public_visitors(self):
        system_source = (settings.BASE_DIR / 'templates/system_index.html').read_text()

        self.assertNotIn('src="{{ STATIC_PREFIX }}time_table.jpg"', system_source)
        self.assertIn("src=\"{% static 'UNI-COT.png' %}\"", system_source)
        self.assertIn('if (OWNER_ADMIN_AUTHENTICATED_ON_LOAD) {\n      publicStartupLoads.push(loadUserData({ silent: true }));', system_source)
        self.assertIn('member system page must not request it', system_source)

    def test_member_directory_has_explicit_states_and_tenant_tab_sync(self):
        groups_source = (settings.BASE_DIR / 'templates/groups.html').read_text()
        system_source = (settings.BASE_DIR / 'templates/system_index.html').read_text()
        sync_source = (settings.BASE_DIR / 'static/js/tenant-sync.js').read_text()

        self.assertIn("membersLoadState === 'loading'", groups_source)
        self.assertIn("membersLoadState === 'error'", groups_source)
        self.assertIn("setMembersLoadState(members.length ? 'data' : 'empty')", groups_source)
        self.assertIn('if (!list.length)', groups_source)
        self.assertNotIn('Owner sign-in is required', groups_source)
        self.assertIn("const usersPath = OWNER_DIRECTORY_ACCESS ? '/api/users/' : '/api/public-members/';", groups_source)
        self.assertIn('Could not load members, retry.', groups_source)
        self.assertIn('Too many requests, retrying shortly.', groups_source)
        self.assertIn("window.TenantLive.register({\n        resource: 'members'", groups_source)
        self.assertIn("window.TenantLive.register({\n        resource: 'groups'", groups_source)
        self.assertIn('publishTenantMutation', system_source)
        self.assertIn('window.TenantSync.subscribe', system_source)
        self.assertIn('fetchAllApiPages(usersPath, rows =>', groups_source)
        self.assertIn('The owner form must use the same configured framework', system_source)
        self.assertLess(len(sync_source.encode('utf-8')), 2048)

    def test_external_signup_uses_public_signup_endpoint_and_spinner_failsafe(self):
        system_source = (settings.BASE_DIR / 'templates/system_index.html').read_text()
        external_source = (settings.BASE_DIR / 'templates/external_tables.html').read_text()
        self.assertIn("fetch('/api/validate-registration/'", system_source)
        self.assertIn("fetch('/api/external-tables/signup/'", system_source)
        self.assertIn('let loadingSpinnerFailsafe = null;', system_source)
        self.assertIn('let loadingSpinnerFailsafe = null;', external_source)
        self.assertIn('setTimeout(hideLoadingSpinner, 60000)', external_source)

@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)
class FounderLoginTests(CacheIsolationMixin, TestCase):
    def test_founder_can_log_in_with_email_and_password(self):
        founder = get_user_model().objects.create_user(
            username="founder",
            email="founder@example.com",
            password="secret123",
            is_staff=True,
        )

        response = self.client.post(
            "/founder/login/",
            {
                "action": "founder_login",
                "email": founder.email,
                "password": "secret123",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Founder Control")


class FounderDashboardQueryTests(CacheIsolationMixin, TestCase):
    def test_tenant_page_uses_a_bounded_number_of_public_queries(self):
        for index in range(26):
            owner = OwnerUser.objects.create(
                email=f"owner-{index}@example.com",
                program_name=f"Program {index}",
                password="hashed",
            )
            tenant = CRTenant.objects.create(
                name=owner.program_name,
                schema_name=f"program_{index}",
                subdomain=f"program-{index}",
                tenant_key=f"tenantkey{index:011d}",
                owner=owner,
            )
            TenantDashboardMetric.objects.create(tenant=tenant, member_count=index)

        # django-tenants emits SET search_path before each of the five bounded
        # public queries (count, tenants, domains, subscriptions, metrics).
        with self.assertNumQueries(10):
            page, metrics = views._founder_tenant_page(1)
            self.assertEqual(len(page.object_list), 25)
            self.assertEqual(len(metrics), 25)

    def test_dashboard_renders_a_page_with_a_tenant(self):
        founder = get_user_model().objects.create_user(
            username="founder-dashboard",
            email="founder-dashboard@example.com",
            password="secret123",
            is_staff=True,
        )
        owner = OwnerUser.objects.create(
            email="dashboard-owner@example.com",
            program_name="Dashboard Program",
            password="hashed",
        )
        tenant = CRTenant.objects.create(
            name=owner.program_name,
            schema_name="dashboard_program",
            subdomain="dashboard-program",
            tenant_key="dashkey1234567890123",
            owner=owner,
        )
        TenantDashboardMetric.objects.create(tenant=tenant, member_count=1)

        self.client.force_login(founder)
        response = self.client.get(reverse("founder_saas_system_control"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, owner.email)

    def test_founder_can_reset_tenant_owner_password(self):
        founder = get_user_model().objects.create_user(
            username="founder-password-reset",
            email="founder-password-reset@example.com",
            password="secret123",
            is_staff=True,
        )
        owner = OwnerUser.objects.create(
            email="password-reset-owner@example.com",
            program_name="Password Reset Program",
            password=make_password("initial-password"),
        )
        tenant = CRTenant.objects.create(
            name=owner.program_name,
            schema_name="password_reset_program",
            subdomain="password-reset-program",
            tenant_key="passresetkey12345678",
            owner=owner,
        )
        self.client.force_login(founder)

        # The request executes four application queries plus django-tenants'
        # SET search_path statement before each query.
        with self.assertNumQueries(8):
            response = self.client.post(reverse("founder_saas_system_control"), {
                "action": "reset_owner_password",
                "tenant_id": tenant.id,
                "new_password": "replacement-password",
                "confirm_password": "replacement-password",
            })

        owner.refresh_from_db()
        self.assertRedirects(response, reverse("founder_saas_system_control"))
        self.assertTrue(check_password("replacement-password", owner.password))

    def test_founder_password_reset_rejects_mismatch(self):
        founder = get_user_model().objects.create_user(
            username="founder-password-mismatch",
            email="founder-password-mismatch@example.com",
            password="secret123",
            is_staff=True,
        )
        owner = OwnerUser.objects.create(
            email="password-mismatch-owner@example.com",
            program_name="Mismatch Program",
            password=make_password("initial-password"),
        )
        tenant = CRTenant.objects.create(
            name=owner.program_name,
            schema_name="password_mismatch_program",
            subdomain="password-mismatch-program",
            tenant_key="passmis1234567890123",
            owner=owner,
        )
        original_hash = owner.password
        self.client.force_login(founder)

        response = self.client.post(reverse("founder_saas_system_control"), {
            "action": "reset_owner_password",
            "tenant_id": tenant.id,
            "new_password": "replacement-password",
            "confirm_password": "different-password",
        })

        owner.refresh_from_db()
        self.assertRedirects(response, reverse("founder_saas_system_control"))
        self.assertEqual(owner.password, original_hash)


class ExternalTableRecordCountTests(TestCase):
    def test_increment_updates_counter_without_a_count_query(self):
        table = ExternalTable.objects.create(table_name="counter_test")

        with self.assertNumQueries(2):
            views._increment_external_table_record_count(table)

        table.refresh_from_db()
        self.assertEqual(table.record_count, 1)

    def test_decrement_never_makes_counter_negative(self):
        table = ExternalTable.objects.create(table_name="counter_floor", record_count=0)

        views._decrement_external_table_record_count(table)

        table.refresh_from_db()
        self.assertEqual(table.record_count, 0)


class ExternalTableRecordRegistrationTests(CacheIsolationMixin, TestCase):
    def test_signup_endpoint_uses_bounded_queries(self):
        table = ExternalTable.objects.create(table_name="signup_query_budget")
        User.objects.create(
            full_name="Signup User",
            registration_number="REG-QUERY-001",
        )
        request = RequestFactory().post(
            "/api/external-tables/signup/",
            data=json.dumps(
                {
                    "table_id": table.id,
                    "registration_number": "REG-QUERY-001",
                    "name": "Signup User",
                    "email": "signup@example.com",
                    "fields": {"course": "Computer Science"},
                }
            ),
            content_type="application/json",
        )

        with (
            patch("core.views._rate_limit", return_value=False),
            patch("core.views._scope_external_tables_queryset", return_value=ExternalTable.objects.all()),
            patch("core.views._scope_users_queryset", return_value=User.objects.all()),
            # django-tenants emits six schema switches; transaction.atomic()
            # emits a savepoint/release pair in TestCase, alongside five
            # bounded business queries.
            self.assertNumQueries(14),
        ):
            response = views.api_external_table_signup(request)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(
            ExternalTableRecord.objects.get(table=table).data["course"],
            "Computer Science",
        )

    def test_registration_number_is_unique_within_a_table(self):
        table = ExternalTable.objects.create(table_name="registration_constraint")
        ExternalTableRecord.objects.create(
            table=table,
            data={"registration_number": "REG-001"},
        )

        with self.assertRaises(IntegrityError):
            ExternalTableRecord.objects.create(
                table=table,
                data={"registration_number": "REG-001"},
            )

    def test_records_without_a_registration_number_are_not_constrained(self):
        table = ExternalTable.objects.create(table_name="unidentified_records")
        ExternalTableRecord.objects.create(table=table, data={"note": "first"})
        ExternalTableRecord.objects.create(table=table, data={"note": "second"})

        self.assertEqual(table.records.count(), 2)


@override_settings(
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    SESSION_ENGINE="django.contrib.sessions.backends.db",
)
class TenantSystemIsolationTests(CacheIsolationMixin, TestCase):
    def setUp(self):
        self.owner, owner_created = OwnerUser.objects.get_or_create(
            email="owner-api@example.com",
            defaults={
                "program_name": "BCIT",
                "password": make_password("secret123"),
                "is_owner": True,
                "is_active": True,
            },
        )
        if owner_created:
            self.client.post(
                "/service/register/",
                {
                    "program_name": "BCIT",
                    "email": "owner-api@example.com",
                    "password": "secret123",
                    "confirm_password": "secret123",
                },
                follow=True,
            )

        self.tenant = getattr(self.owner, "tenant", None)
        if self.tenant is None:
            from customers.models import TenantSubscription

            self.tenant = CRTenant.objects.create(
                name="BCIT",
                schema_name="bcit_tenant_isolation",
                subdomain="bcit-tenant-isolation",
                tenant_key="tenantkey01234567890",
                owner=self.owner,
                is_active=True,
                provisioning_state=CRTenant.PROVISIONING_READY,
                paid_until=None,
                subscription_start=None,
                is_trial=True,
            )
            TenantSubscription.objects.create(
                tenant=self.tenant,
                plan="trial",
                status=TenantSubscription.STATUS_TRIAL,
                start_date=self.tenant.created_on,
                end_date=self.tenant.created_on + timedelta(days=14),
                is_active=True,
            )
        elif self.tenant.provisioning_state != CRTenant.PROVISIONING_READY:
            self.tenant.provisioning_state = CRTenant.PROVISIONING_READY
            self.tenant.save(update_fields=["provisioning_state"])

        session = self.client.session
        session["service_user"] = {
            "user_type": "owner",
            "owner_id": self.owner.id,
            "email": self.owner.email,
            "program_name": self.owner.program_name,
        }
        session["tenant_id"] = self.tenant.id
        session["tenant_subdomain"] = self.tenant.subdomain
        session["tenant_key"] = self.tenant.tenant_key
        session.save()

    def test_owner_session_renders_management_timetable(self):
        with schema_context('public'):
            FounderTimetableSource.objects.update_or_create(
                pk=1,
                defaults={'file': 'system/timetables/test.xlsx', 'original_filename': 'test.xlsx', 'version': 1},
            )
        with schema_context(self.tenant.schema_name):
            TenantTimetableState.objects.filter(pk=1).delete()
        request = RequestFactory().get('/timetable/?manage=1')
        request.tenant = self.tenant
        request.session = {'service_user': {'user_type': 'owner', 'owner_id': self.owner.id}}
        with schema_context(self.tenant.schema_name):
            with self.assertNumQueries(3):
                response = views.timetable(request)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="timetable-manager"')
        self.assertContains(response, 'id="managerCohortNav"')
        self.assertContains(response, 'id="publishTimetableBtn"')
        self.assertContains(response, 'const OWNER_SETUP_MODE = true;')

    def test_timetable_setup_mode_only_opens_for_missing_or_stale_publication(self):
        with schema_context('public'):
            source, _ = FounderTimetableSource.objects.update_or_create(
                pk=1,
                defaults={'file': 'system/timetables/test.xlsx', 'original_filename': 'test.xlsx', 'version': 4},
            )
        with schema_context(self.tenant.schema_name):
            TenantTimetableState.objects.update_or_create(
                pk=1,
                defaults={
                    'source_version': source.version,
                    'revision': 1,
                    'state': {'published': True, 'schedules': [{'id': 'lesson-1'}]},
                },
            )
        request = RequestFactory().get('/timetable/?manage=1&setup=1')
        request.tenant = self.tenant
        request.session = {'service_user': {'user_type': 'owner', 'owner_id': self.owner.id}}
        with schema_context(self.tenant.schema_name):
            with self.assertNumQueries(3):
                published = views.timetable(request)
        self.assertContains(published, 'const OWNER_SETUP_MODE = false;')
        self.assertNotContains(published, 'id="publishTimetableBtn"')

        with schema_context('public'):
            FounderTimetableSource.objects.filter(pk=1).update(version=5)
        with schema_context(self.tenant.schema_name):
            with self.assertNumQueries(3):
                stale = views.timetable(request)
        self.assertContains(stale, 'const OWNER_SETUP_MODE = true;')
        self.assertContains(stale, 'id="publishTimetableBtn"')

    def test_tenant_users_api_requires_owner_session(self):
        self.client.post(f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/logout/", follow=True)

        response = self.client.get(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/users/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 403)
        self.assertJSONEqual(
            response.content,
            {"success": False, "error": "Owner login required for this tenant system"},
        )

    def test_timetable_state_is_owner_writable_and_query_bounded(self):
        with schema_context('public'):
            FounderTimetableSource.objects.update_or_create(
                pk=1,
                defaults={
                    'file': 'system/timetables/test.xlsx',
                    'original_filename': 'test.xlsx',
                    'version': 1,
                },
            )
        request = RequestFactory().get('/api/timetable/state/')
        request.tenant = self.tenant
        request.session = {'service_user': {'user_type': 'owner', 'owner_id': self.owner.id}}
        with schema_context(self.tenant.schema_name):
            with self.assertNumQueries(6):
                response = views.api_timetable_state(request)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['ETag'].startswith('"timetable-1-0-'))
        request.META['HTTP_IF_NONE_MATCH'] = response['ETag']
        with schema_context(self.tenant.schema_name):
            with self.assertNumQueries(6):
                unchanged = views.api_timetable_state(request)
        self.assertEqual(unchanged.status_code, 304)

        request = RequestFactory().put(
            '/api/timetable/state/',
            data=json.dumps({'expected_revision': 0, 'state': {'schedules': [{
                'id': 'lesson-monday-0800', 'status': 'HAKIPO', 'cohort': 'BAE (54)', 'code': 'AEU 08101',
                'day': 'Monday', 'startSlot': 0, 'duration': 1,
                'isActivity': True, 'swapType': 'temporary', 'swapPairId': 'pair-1',
                'originalDay': 'Tuesday', 'originalStartSlot': 3,
            }], 'venues': [], 'published': True}, 'source_version': 1}),
            content_type='application/json',
        )
        request.tenant = self.tenant
        request.session = {'service_user': {'user_type': 'owner', 'owner_id': self.owner.id}}
        with schema_context(self.tenant.schema_name):
            with self.assertNumQueries(18):
                response = views.api_timetable_state(request)
            self.assertEqual(response.status_code, 200)
            saved_schedule = TenantTimetableState.objects.get(pk=1).state['schedules'][0]
            self.assertTrue(saved_schedule['isActivity'])
            self.assertEqual(saved_schedule['swapPairId'], 'pair-1')
            self.assertEqual(saved_schedule['originalDay'], 'Tuesday')
            self.assertEqual(saved_schedule['originalStartSlot'], 3)
            self.assertEqual(saved_schedule['status'], 'HAKIPO')
            self.assertEqual(TenantTimetableState.objects.get(pk=1).revision, 1)

        request = RequestFactory().get('/api/timetable/state/')
        request.tenant = self.tenant
        request.session = {'service_user': {'user_type': 'member', 'member_id': 1, 'owner_id': self.owner.id}}
        with schema_context(self.tenant.schema_name):
            with self.assertNumQueries(5):
                member_response = views.api_timetable_state(request)
        self.assertEqual(member_response.json()['timetable_status'], 'published')
        self.assertEqual(member_response.json()['data']['schedules'][0]['status'], 'HAKIPO')

        stale_save = RequestFactory().put(
            '/api/timetable/state/',
            data=json.dumps({'expected_revision': 0, 'source_version': 1, 'state': {
                'schedules': [{'id': 'lesson-monday-0800', 'day': 'Monday', 'startSlot': 0, 'duration': 1, 'status': 'KIPO'}],
                'venues': [], 'published': True,
            }}),
            content_type='application/json',
        )
        stale_save.tenant = self.tenant
        stale_save.session = {'service_user': {'user_type': 'owner', 'owner_id': self.owner.id}}
        with schema_context(self.tenant.schema_name):
            conflict = views.api_timetable_state(stale_save)
            persisted = TenantTimetableState.objects.get(pk=1)
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(persisted.revision, 1)
        self.assertEqual(persisted.state['schedules'][0]['status'], 'HAKIPO')

    def test_tenant_timetable_is_hidden_until_source_and_owner_publish(self):
        request = RequestFactory().get('/api/timetable/state/')
        request.tenant = self.tenant
        request.session = {'service_user': {'user_type': 'member', 'member_id': 1, 'owner_id': self.owner.id}}
        with schema_context('public'):
            FounderTimetableSource.objects.filter(pk=1).delete()
        with schema_context(self.tenant.schema_name):
            with self.assertNumQueries(5):
                missing = views.api_timetable_state(request)
        self.assertEqual(missing.json()['timetable_status'], 'not_provided')
        self.assertIsNone(missing.json()['data'])

        with schema_context('public'):
            FounderTimetableSource.objects.create(
                pk=1,
                file='system/timetables/test.xlsx',
                original_filename='test.xlsx',
                version=3,
            )
        draft_state = {'schedules': [{'day': 'Monday', 'startSlot': 0, 'duration': 1}], 'published': False}
        with schema_context(self.tenant.schema_name):
            TenantTimetableState.objects.update_or_create(
                pk=1,
                defaults={'state': draft_state, 'source_version': 3},
            )
            with self.assertNumQueries(5):
                hidden = views.api_timetable_state(request)
        self.assertEqual(hidden.json()['timetable_status'], 'owner_setup')
        self.assertIsNone(hidden.json()['data'])

        request.session = {'service_user': {'user_type': 'owner', 'owner_id': self.owner.id}}
        with schema_context(self.tenant.schema_name):
            with self.assertNumQueries(6):
                owner_view = views.api_timetable_state(request)
        self.assertEqual(owner_view.json()['timetable_status'], 'owner_setup')
        self.assertEqual(owner_view.json()['data'], draft_state)
        self.assertIn('url', owner_view.json()['source'])

        published_state = {**draft_state, 'published': True}
        with schema_context(self.tenant.schema_name):
            TenantTimetableState.objects.filter(pk=1).update(state=published_state)
        request.session = {'service_user': {'user_type': 'member', 'member_id': 1, 'owner_id': self.owner.id}}
        with schema_context(self.tenant.schema_name):
            with self.assertNumQueries(5):
                visible = views.api_timetable_state(request)
        self.assertEqual(visible.json()['timetable_status'], 'published')
        self.assertEqual(visible.json()['data'], published_state)
        self.assertNotIn('url', visible.json()['source'])

    def test_timetable_state_rejects_member_and_invalid_schedule(self):
        with schema_context('public'):
            FounderTimetableSource.objects.update_or_create(
                pk=1,
                defaults={'file': 'system/timetables/test.xlsx', 'original_filename': 'test.xlsx', 'version': 1},
            )
        request = RequestFactory().put(
            '/api/timetable/state/', data=json.dumps({'state': {'schedules': []}}), content_type='application/json',
        )
        request.tenant = self.tenant
        request.session = {'service_user': {'user_type': 'member', 'member_id': 1, 'owner_id': self.owner.id}}
        request.META['HTTP_X_REQUESTED_WITH'] = 'XMLHttpRequest'
        with schema_context(self.tenant.schema_name):
            denied = views.api_timetable_state(request)
        self.assertEqual(denied.status_code, 403)

        request = RequestFactory().put(
            '/api/timetable/state/',
            data=json.dumps({'expected_revision': 0, 'state': {'schedules': [{'id': 'invalid-slot', 'day': 'Monday', 'startSlot': 13, 'duration': 2}]}}),
            content_type='application/json',
        )
        request.tenant = self.tenant
        request.session = {'service_user': {'user_type': 'owner', 'owner_id': self.owner.id}}
        with schema_context(self.tenant.schema_name):
            invalid = views.api_timetable_state(request)
        self.assertEqual(invalid.status_code, 400)

        request = RequestFactory().put('/api/timetable/state/', data=b'\xff', content_type='application/json')
        request.tenant = self.tenant
        request.session = {'service_user': {'user_type': 'owner', 'owner_id': self.owner.id}}
        malformed = views.api_timetable_state(request)
        self.assertEqual(malformed.status_code, 400)

        duplicate = {'expected_revision': 0, 'state': {'schedules': [
            {'id': 'same-id', 'day': 'Monday', 'startSlot': 0, 'duration': 1},
            {'id': 'same-id', 'day': 'Tuesday', 'startSlot': 0, 'duration': 1},
        ], 'venues': [], 'published': True}, 'source_version': 1}
        request = RequestFactory().put(
            '/api/timetable/state/', data=json.dumps(duplicate), content_type='application/json',
        )
        request.tenant = self.tenant
        request.session = {'service_user': {'user_type': 'owner', 'owner_id': self.owner.id}}
        duplicate_response = views.api_timetable_state(request)
        self.assertEqual(duplicate_response.status_code, 400)

    def test_timetable_state_write_rejects_cross_tenant_and_inactive_owner(self):
        payload = json.dumps({'state': {'schedules': [], 'venues': [], 'published': False}, 'source_version': 1})
        request = RequestFactory().put('/t/other/999/key/api/timetable/state/', data=payload, content_type='application/json')
        request.tenant = SimpleNamespace(
            schema_name='other_tenant_schema', owner_id=self.owner.id + 1, is_active=True,
        )
        request.session = {'service_user': {'user_type': 'owner', 'owner_id': self.owner.id}}
        with patch('service.views._get_owner_by_id', return_value=self.owner), self.assertNumQueries(0):
            cross_tenant = views.api_timetable_state(request)
        self.assertEqual(cross_tenant.status_code, 403)

        request.tenant = self.tenant
        with patch('service.views._get_owner_by_id', return_value=None), self.assertNumQueries(0):
            inactive_owner = views.api_timetable_state(request)
        self.assertEqual(inactive_owner.status_code, 403)

    def test_timetable_owner_cannot_save_before_founder_upload(self):
        with schema_context('public'):
            FounderTimetableSource.objects.filter(pk=1).delete()
        request = RequestFactory().put(
            '/api/timetable/state/',
            data=json.dumps({'state': {'schedules': [], 'venues': [], 'published': False}, 'source_version': 0}),
            content_type='application/json',
        )
        request.tenant = self.tenant
        request.session = {'service_user': {'user_type': 'owner', 'owner_id': self.owner.id}}
        with schema_context(self.tenant.schema_name):
            response = views.api_timetable_state(request)
        self.assertEqual(response.status_code, 409)
        self.assertIn('provider has not supplied', response.json()['error'])

    def test_founder_timetable_source_endpoint_is_staff_only_and_query_bounded(self):
        request = RequestFactory().get('/api/founder/timetable-source/')
        request.user = get_user_model()(is_active=True, is_staff=False)
        denied = views.api_founder_timetable_source(request)
        self.assertEqual(denied.status_code, 403)

        request.user = get_user_model()(is_active=True, is_staff=True)
        with schema_context('public'):
            FounderTimetableSource.objects.filter(pk=1).delete()
            with self.assertNumQueries(4):
                response = views.api_founder_timetable_source(request)
        self.assertEqual(response.status_code, 200)

    def test_founder_timetable_delete_keeps_version_monotonic_for_reupload(self):
        with schema_context('public'):
            FounderTimetableSource.objects.create(
                pk=1,
                file='system/timetables/original.xlsx',
                original_filename='original.xlsx',
                version=7,
            )
        user = get_user_model()(is_active=True, is_staff=True, id=9001)
        request = RequestFactory().delete('/api/founder/timetable-source/')
        request.user = user
        with schema_context('public'):
            with self.assertNumQueries(4):
                response = views.api_founder_timetable_source(request)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['data'])
        with schema_context('public'):
            source = FounderTimetableSource.objects.get(pk=1)
            self.assertEqual(source.version, 8)
            self.assertFalse(source.file)

        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            upload = SimpleUploadedFile('replacement.xlsx', b'workbook')
            request = RequestFactory().post('/api/founder/timetable-source/', {'file': upload})
            request.user = user
            with schema_context('public'):
                with self.assertNumQueries(5):
                    response = views.api_founder_timetable_source(request)
            self.assertEqual(response.status_code, 200)
        self.assertGreater(response.json()['data']['version'], 8)

    def test_tenant_users_api_rejects_member_and_other_tenant_owner_sessions(self):
        path = f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/users/"
        session = self.client.session
        session["service_user"] = {"user_type": "member", "member_id": 1, "owner_id": self.owner.id}
        session.save()
        member_response = self.client.get(path, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

        other_owner = OwnerUser.objects.create(
            email="different-owner@example.com",
            program_name="Different",
            password="hash",
            is_owner=True,
            is_active=True,
        )
        session = self.client.session
        session["service_user"] = {"user_type": "owner", "owner_id": other_owner.id}
        session.save()
        other_owner_response = self.client.get(path, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

        for response in (member_response, other_owner_response):
            self.assertEqual(response.status_code, 403)
            self.assertJSONEqual(
                response.content,
                {"success": False, "error": "Owner login required for this tenant system"},
            )

    def test_tenant_users_api_returns_only_current_owner_records(self):
        other_owner = OwnerUser.objects.create(
            email="owner-other@example.com",
            program_name="Other",
            password="hash",
            is_owner=True,
            is_active=True,
        )
        User.objects.create(full_name="Owner One User", registration_number="BCIT-001", created_by=self.owner.id)
        User.objects.create(full_name="Other User", registration_number="OTHER-001", created_by=other_owner.id)

        response = self.client.get(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/users/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertEqual(len(payload["data"]), 1)
        self.assertEqual(payload["data"][0]["registration_number"], "BCIT-001")

    def test_bulk_clear_users_and_groups_is_bounded_and_tenant_scoped(self):
        other_owner = OwnerUser.objects.create(
            email="bulk-clear-other@example.com",
            program_name="Other",
            password="hash",
            is_owner=True,
            is_active=True,
        )
        own_user = User.objects.create(
            full_name="Delete Me", registration_number="CLEAR-001", created_by=self.owner.id,
        )
        other_user = User.objects.create(
            full_name="Keep Me", registration_number="CLEAR-OTHER-001", created_by=other_owner.id,
        )
        own_group = UserGroup.objects.create(group_name="Clear Group", created_by=self.owner.id)
        other_group = UserGroup.objects.create(group_name="Other Group", created_by=other_owner.id)
        own_membership = UserGroupMember.objects.create(user_id=own_user.id, group_id=own_group.id)
        other_membership = UserGroupMember.objects.create(user_id=other_user.id, group_id=other_group.id)

        request = RequestFactory().post(
            "/api/users/clear-all/",
            data=json.dumps({"include_groups": True}),
            content_type="application/json",
        )
        request.tenant = self.tenant
        request.session = {"service_user": {"user_type": "owner", "owner_id": self.owner.id}}

        with schema_context(self.tenant.schema_name):
            with self.assertNumQueries(14):
                response = views.api_users_clear_all(request)

        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(result["users_deleted"], 1)
        self.assertEqual(result["groups_deleted"], 1)
        self.assertFalse(User.objects.filter(id=own_user.id).exists())
        self.assertTrue(User.objects.filter(id=other_user.id).exists())
        self.assertFalse(UserGroup.objects.filter(id=own_group.id).exists())
        self.assertTrue(UserGroup.objects.filter(id=other_group.id).exists())
        self.assertFalse(UserGroupMember.objects.filter(id=own_membership.id).exists())
        self.assertTrue(UserGroupMember.objects.filter(id=other_membership.id).exists())

    def test_owner_admin_login_can_create_then_list_users_on_ready_tenant(self):
        tenant_path = f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}"
        login_response = self.client.post(
            f"{tenant_path}/api/owner-admin-login/",
            data=json.dumps({
                "email": self.owner.email,
                "password": "secret123",
                "tenant_slug": self.tenant.subdomain,
                "tenant_id": self.tenant.id,
            }),
            content_type="application/json",
        )
        self.assertEqual(login_response.status_code, 200, login_response.content)

        create_response = self.client.post(
            f"{tenant_path}/api/users/",
            data=json.dumps({
                "full_name": "Owner Created User",
                "registration_number": "BCIT-OWNER-001",
            }),
            content_type="application/json",
        )
        self.assertEqual(create_response.status_code, 201, create_response.content)

        for suffix in ("", "?page_size=100&page=1"):
            with self.subTest(suffix=suffix):
                response = self.client.get(f"{tenant_path}/api/users/{suffix}")
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json()["data"][0]["registration_number"], "BCIT-OWNER-001")

    def test_tenant_signup_setting_get_is_public(self):
        self.client.post(f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/logout/", follow=True)

        response = self.client.get(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/signup-setting/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload["success"])

    def test_groups_page_and_groups_api_get_are_public(self):
        self.client.post(f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/logout/", follow=True)

        page_response = self.client.get(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/groups/",
        )
        api_response = self.client.get(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/groups/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(page_response.status_code, 200)
        self.assertContains(page_response, 'const OWNER_DIRECTORY_ACCESS = false;')
        self.assertNotContains(page_response, 'Group summaries are available')
        self.assertNotContains(page_response, 'Owner sign-in is required')
        self.assertEqual(api_response.status_code, 200)
        payload = json.loads(api_response.content)
        self.assertTrue(payload["success"])

    def test_owner_groups_page_receives_same_tenant_directory_and_groups(self):
        tenant_path = f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}"
        group_response = self.client.post(
            f"{tenant_path}/api/groups/",
            data=json.dumps({"group_name": "System Group", "max_members": 10}),
            content_type="application/json",
        )
        self.assertEqual(group_response.status_code, 201, group_response.content)
        user_response = self.client.post(
            f"{tenant_path}/api/users/",
            data=json.dumps({
                "full_name": "System Member",
                "registration_number": "SYSTEM-001",
                "group_name": "System Group",
            }),
            content_type="application/json",
        )
        self.assertEqual(user_response.status_code, 201, user_response.content)

        page_response = self.client.get(f"{tenant_path}/groups/")
        groups_response = self.client.get(f"{tenant_path}/api/groups/?include_members=1")
        users_response = self.client.get(f"{tenant_path}/api/users/?page_size=100&page=1")

        self.assertEqual(page_response.status_code, 200)
        self.assertContains(page_response, 'const OWNER_DIRECTORY_ACCESS = true;')
        self.assertNotContains(page_response, 'Group summaries are available')
        self.assertEqual(users_response.status_code, 200)
        self.assertEqual(users_response.json()["data"][0]["registration_number"], "SYSTEM-001")
        self.assertEqual(groups_response.status_code, 200)
        self.assertEqual(groups_response.json()["data"][0]["members"][0]["display_name"], "System Member")

    def test_public_group_member_summary_has_contact_details_for_group_members(self):
        group = UserGroup.objects.create(
            group_name="Public Group", max_members=10, created_by=self.owner.id
        )
        user = User.objects.create(
            full_name="Visible Group Member",
            registration_number="PUBLIC-GROUP-001",
            email="private@example.com",
            phone="123456",
            created_by=self.owner.id,
            group_name=group.group_name,
        )
        UserGroupMember.objects.create(user_id=user.id, group_id=group.id, status="active")

        self.client.post(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/logout/",
            follow=True,
        )
        path = f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/groups/?include_members=1"
        # django-tenants emits four schema switches and pagination counts the
        # group queryset. The final bulk user-name query makes this bounded at
        # ten queries, regardless of how many members the group contains.
        with self.assertNumQueries(10):
            response = self.client.get(path, HTTP_X_REQUESTED_WITH="XMLHttpRequest")

        self.assertEqual(response.status_code, 200)
        member = response.json()["data"][0]["members"][0]
        self.assertEqual(member["display_name"], "Visible Group Member")
        self.assertEqual(member["registration_number"], "PUBLIC-GROUP-001")
        self.assertEqual(member["phone"], "123456")
        self.assertEqual(member["group_name"], "Public Group")

    def test_public_members_endpoint_lists_active_member_fields_without_owner_auth(self):
        User.objects.create(
            full_name="Public Directory Member",
            registration_number="PUBLIC-002",
            phone="5550100",
            case_info="Needs follow-up",
            created_by=self.owner.id,
        )
        self.client.post(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/logout/",
            follow=True,
        )
        response = self.client.get(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/public-members/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(response.status_code, 200)
        member = response.json()["data"][0]
        self.assertEqual(member["full_name"], "Public Directory Member")
        self.assertEqual(member["phone"], "5550100")
        self.assertEqual(member["case_info"], "Needs follow-up")

    def test_properties_and_external_tables_pages_are_public(self):
        self.client.post(f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/logout/", follow=True)

        properties_response = self.client.get(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/properties/",
        )
        tables_response = self.client.get(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/external-tables/",
        )

        self.assertEqual(properties_response.status_code, 200)
        self.assertEqual(tables_response.status_code, 200)

    def test_public_service_external_tables_page_returns_200(self):
        response = self.client.get('/service/external-tables/')
        self.assertEqual(response.status_code, 200)

    def test_public_service_external_tables_api_returns_empty_list(self):
        response = self.client.get('/service/api/external-tables/', HTTP_X_REQUESTED_WITH='XMLHttpRequest')
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.content)
        self.assertTrue(payload['success'])
        self.assertEqual(payload['data'], [])
        self.assertEqual(payload['meta']['total_count'], 0)

    def test_tenant_properties_api_persists_to_database_and_is_publicly_readable(self):
        create_response = self.client.post(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/properties/",
            data=json.dumps(
                {
                    "item_name": "Black Wallet",
                    "description": "Wallet found near the hall",
                    "category": "other",
                    "location": "Main Hall",
                    "date_found": "2026-04-16",
                    "image_url": "https://example.com/wallet.jpg",
                    "contact_info": "finder@example.com",
                }
            ),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(create_response.status_code, 201)

        self.client.post(f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/logout/", follow=True)

        list_response = self.client.get(
            f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/properties/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(list_response.status_code, 200)
        payload = json.loads(list_response.content)
        self.assertTrue(payload["success"])
        self.assertEqual(len(payload["data"]), 1)
        self.assertEqual(payload["data"][0]["item_name"], "Black Wallet")

    def test_user_group_assignment_and_move_keep_memberships_in_sync(self):
        base_path = f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api"
        first_group = UserGroup.objects.create(
            group_name="Group A", max_members=10, created_by=self.owner.id
        )
        second_group = UserGroup.objects.create(
            group_name="Group B", max_members=10, created_by=self.owner.id
        )

        create_response = self.client.post(
            f"{base_path}/users/",
            data=json.dumps({
                "full_name": "Grouped Member",
                "registration_number": "GROUP-001",
                "group_name": first_group.group_name,
            }),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(create_response.status_code, 201)
        user_id = create_response.json()["data"]["id"]

        first_group.refresh_from_db()
        self.assertEqual(first_group.current_members, 1)
        self.assertTrue(UserGroupMember.objects.filter(
            user_id=user_id, group_id=first_group.id, status="active"
        ).exists())

        move_response = self.client.post(
            f"{base_path}/groups/move-member/",
            data=json.dumps({"user_id": user_id, "target_group_id": second_group.id}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(move_response.status_code, 200)
        self.assertFalse(UserGroupMember.objects.filter(
            user_id=user_id, group_id=first_group.id, status="active"
        ).exists())
        self.assertTrue(UserGroupMember.objects.filter(
            user_id=user_id, group_id=second_group.id, status="active"
        ).exists())
        self.assertEqual(User.objects.get(id=user_id).group_name, second_group.group_name)

        # The system-index "add member" action uses this user update endpoint.
        # It must recreate the canonical membership rather than only changing
        # the legacy group_name string.
        update_response = self.client.put(
            f"{base_path}/users/{user_id}/",
            data=json.dumps({"group_name": first_group.group_name}),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(update_response.status_code, 200)
        membership = UserGroupMember.objects.get(user_id=user_id, group_id=first_group.id)
        self.assertEqual(User.objects.get(id=user_id).group_name, first_group.group_name)

        # Removing that membership must also clear the denormalized user field
        # so the groups and user-management screens cannot disagree.
        remove_response = self.client.delete(
            f"{base_path}/group-members/{membership.id}/",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(remove_response.status_code, 200)
        self.assertEqual(User.objects.get(id=user_id).group_name, "")

        groups_response = self.client.get(
            f"{base_path}/groups/?include_members=1", HTTP_X_REQUESTED_WITH="XMLHttpRequest"
        )
        groups_by_id = {group["id"]: group for group in groups_response.json()["data"]}
        self.assertEqual(groups_by_id[first_group.id]["members"], [])
        self.assertEqual(groups_by_id[second_group.id]["members"], [])

    def test_public_property_claim_cannot_change_owner_managed_fields(self):
        prop = Property.objects.create(
            item_name="Original Wallet",
            description="Found by reception",
            category="other",
            location="Reception",
            created_by=self.owner.id,
        )
        path = f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/properties/{prop.id}/"
        self.client.post(f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/logout/", follow=True)

        response = self.client.put(
            path,
            data=json.dumps({
                "status": "claimed",
                "claimant_name": "Claimant",
                "claimant_contact": "claimant@example.com",
                "claim_proof": "Receipt",
                "item_name": "Attempted overwrite",
                "location": "Attempted overwrite",
            }),
            content_type="application/json",
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        prop.refresh_from_db()
        self.assertEqual(prop.status, "claimed")
        self.assertEqual(prop.claimant_name, "Claimant")
        self.assertEqual(prop.item_name, "Original Wallet")
        self.assertEqual(prop.location, "Reception")

    def test_manual_membership_add_keeps_existing_group_and_enforces_owner_scope(self):
        user = User.objects.create(full_name="Multi Group User", registration_number="MULTI-001", created_by=self.owner.id)
        first = UserGroup.objects.create(group_name="Existing Group", created_by=self.owner.id)
        target = UserGroup.objects.create(group_name="Additional Group", created_by=self.owner.id)
        other_owner = OwnerUser.objects.create(email="multi-other@example.com", program_name="Other", password="hash", is_owner=True, is_active=True)
        outside = UserGroup.objects.create(group_name="Outside Scope", created_by=other_owner.id)
        UserGroupMember.objects.create(user_id=user.id, group_id=first.id, status="active")
        base = f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/group-members/"

        response = self.client.post(base, data=json.dumps({"user_id": user.id, "group_id": target.id}), content_type="application/json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(UserGroupMember.objects.filter(user_id=user.id, status="active").values_list("group_id", flat=True)), {first.id, target.id})
        denied = self.client.post(base, data=json.dumps({"user_id": user.id, "group_id": outside.id}), content_type="application/json")
        self.assertEqual(denied.status_code, 404)

    def test_excel_import_skips_duplicates_and_preserves_multiple_group_memberships(self):
        from io import BytesIO
        from openpyxl import Workbook, load_workbook
        from django.core.files.uploadedfile import SimpleUploadedFile

        existing = User.objects.create(full_name="Existing", registration_number="EXCEL-DUP", created_by=self.owner.id)
        existing_group = UserGroup.objects.create(group_name="Prior Group", created_by=self.owner.id)
        UserGroupMember.objects.create(user_id=existing.id, group_id=existing_group.id, status="active")
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Full Name", "Registration Number", "Phone Number", "Groups"])
        sheet.append(["Duplicate Original", "EXCEL-DUP", "111", "Review Group"])
        sheet.append(["New Person", "EXCEL-NEW", "222", "Review Group, Second Group"])
        source = BytesIO()
        workbook.save(source)
        upload = SimpleUploadedFile("users.xlsx", source.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        base = f"/t/{self.tenant.subdomain}/{self.tenant.id}/{self.tenant.tenant_key}/api/users/excel/"

        response = self.client.post(base, {"file": upload}, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"], {"total_rows_processed": 2, "successfully_imported": 1, "duplicates_skipped": 1, "validation_errors": 0})
        existing.refresh_from_db()
        self.assertEqual(existing.full_name, "Existing")
        imported = User.objects.get(registration_number="EXCEL-NEW")
        imported_group_ids = set(UserGroupMember.objects.filter(user_id=imported.id, status="active").values_list("group_id", flat=True))
        self.assertEqual(len(imported_group_ids), 2)
        report = self.client.get(base + "?report=skipped")
        report_sheet = load_workbook(BytesIO(report.content), read_only=True).active
        self.assertEqual(list(report_sheet.iter_rows(min_row=2, values_only=True))[0], ("Duplicate Original", "EXCEL-DUP", "111", "Review Group"))
        export = self.client.get(base)
        export_sheet = load_workbook(BytesIO(export.content), read_only=False).active
        self.assertEqual(tuple(cell.value for cell in export_sheet[1]), ("Full Name", "Registration Number", "Phone Number", "Groups"))
        self.assertEqual(export_sheet["A1"].fill.fgColor.rgb, "FFD1FAE5")
        self.assertEqual(export_sheet["A1"].font.color.rgb, "FF047857")
        self.assertEqual(export_sheet.freeze_panes, "A2")
        self.assertEqual(export_sheet.auto_filter.ref, export_sheet.dimensions)
        self.assertEqual(len(list(export_sheet.iter_rows(min_row=2, values_only=True))), 2)
