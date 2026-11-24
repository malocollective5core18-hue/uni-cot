from datetime import date, time
from contextlib import nullcontext
from io import BytesIO
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase
from django.urls import resolve
from openpyxl import Workbook

from . import views
from .models import FounderExamTimetableSource
from .services import (
    ExamCohortAmbiguous,
    ExamCohortNotFound,
    ExamRecord,
    ExamTimetableError,
    build_exam_pdf,
    extract_cohort_records,
    inspect_exam_workbook,
    normalize_cohort,
)


def make_exam_workbook(sheets):
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets:
        sheet = workbook.create_sheet(title)
        for row in rows:
            sheet.append(row)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


class ExamWorkbookServiceTests(SimpleTestCase):
    def test_cohort_normalization_handles_case_punctuation_spaces_and_counts(self):
        self.assertEqual(normalize_cohort('  batf-stream   a (200) '), 'BATF STREAM A')
        self.assertEqual(normalize_cohort('B.A.E (65)'), 'BAE')

    def test_exact_cohort_match_extracts_only_that_stream(self):
        workbook = make_exam_workbook([('Examinations', [
            ['Programme', 'Date', 'Day', 'Time', 'Course Code', 'Course Name', 'Venue'],
            ['BATF STREAM A (200)', '2026-12-01', 'Tuesday', '09:00 - 12:00', 'AEU 08101', 'Automobile Design', 'Hall A'],
            ['BATF STREAM B (190)', '2026-12-02', 'Wednesday', '13:00 - 15:00', 'AEU 08102', 'Engine Management', 'Hall B'],
        ])])
        records = extract_cohort_records(workbook, 'batf stream a')
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].course_code, 'AEU 08101')
        self.assertEqual(records[0].venue, 'Hall A')

    def test_stream_a_never_falls_back_to_stream_b(self):
        workbook = make_exam_workbook([('Examinations', [
            ['Programme', 'Date', 'Time', 'Course Code'],
            ['BATF STREAM B', '2026-12-01', '09:00 - 10:00', 'AEU 08102'],
        ])])
        with self.assertRaises(ExamCohortNotFound):
            extract_cohort_records(workbook, 'BATF STREAM A')

    def test_unqualified_code_matching_multiple_streams_is_ambiguous(self):
        workbook = make_exam_workbook([('Examinations', [
            ['Programme', 'Date', 'Time', 'Course Code'],
            ['BATF STREAM A', '2026-12-01', '09:00 - 10:00', 'AEU 08101'],
            ['BATF STREAM B', '2026-12-01', '09:00 - 10:00', 'AEU 08102'],
        ])])
        with self.assertRaises(ExamCohortAmbiguous):
            extract_cohort_records(workbook, 'BATF')

    def test_merged_cohort_cells_cover_each_schedule_row(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Examinations'
        for row in [
            ['Class', 'Date', 'Start Time', 'End Time', 'Subject'],
            ['BAE (65)', '2026-12-01', '09:00', '11:00', 'Design'],
            [None, '2026-12-02', '13:00', '15:00', 'Materials'],
        ]:
            sheet.append(row)
        sheet.merge_cells('A2:A3')
        output = BytesIO()
        workbook.save(output)
        records = extract_cohort_records(output.getvalue(), 'BAE')
        self.assertEqual([record.course_name for record in records], ['Design', 'Materials'])

    def test_unrecognized_layout_is_rejected_instead_of_ignored(self):
        workbook = make_exam_workbook([('Sheet1', [['Notes'], ['Exam date TBD']])])
        with self.assertRaisesRegex(ExamTimetableError, 'No supported examination table'):
            inspect_exam_workbook(workbook)

    def test_pdf_is_generated_for_meaningful_records(self):
        record = ExamRecord(
            exam_date=date(2026, 12, 1), day='Tuesday',
            start_time=time(9), end_time=time(11), course_code='AEU 08101',
            course_name='Automobile Design', venue='Hall A',
        )
        pdf = build_exam_pdf([record], 'BAE', 'test1', 2)
        self.assertTrue(pdf.startswith(b'%PDF-'))

    def test_empty_record_list_cannot_generate_pdf(self):
        with self.assertRaisesRegex(ExamTimetableError, 'No examination records'):
            build_exam_pdf([], 'BAE', 'test1', 1)


class ExamApiTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.staff = SimpleNamespace(is_authenticated=True, is_active=True, is_staff=True, id=5)

    def test_examination_source_app_is_shared_but_not_tenant_installed(self):
        from django.conf import settings

        self.assertIn('exams', settings.SHARED_APPS)
        self.assertNotIn('exams', settings.TENANT_APPS)

    def test_founder_and_tenant_exam_urls_resolve(self):
        self.assertEqual(
            resolve('/api/founder/exam-timetable-source/test1/').view_name,
            'api_founder_exam_timetable_source',
        )
        self.assertEqual(
            resolve('/api/timetable/exam-pdf/ue/').view_name,
            'api_tenant_exam_timetable_pdf',
        )

    def test_unauthorized_founder_source_request_is_rejected_before_storage(self):
        request = self.factory.delete('/api/founder/exam-timetable-source/test1/')
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_staff=False)
        response = views.api_founder_exam_timetable_source(request, 'test1')
        self.assertEqual(response.status_code, 403)

    def test_unknown_examination_type_is_rejected(self):
        request = self.factory.get('/api/founder/exam-timetable-source/final/')
        request.user = self.staff
        response = views.api_founder_exam_timetable_source(request, 'final')
        self.assertEqual(response.status_code, 404)

    def test_xls_and_invalid_xlsx_uploads_are_rejected(self):
        request = self.factory.post('/api/founder/exam-timetable-source/test1/')
        request.user = self.staff
        for filename, contents in (('legacy.xls', b'not an xls'), ('broken.xlsx', b'not a workbook')):
            request.FILES['file'] = SimpleUploadedFile(filename, contents)
            with patch.object(views, 'schema_context', return_value=nullcontext()):
                response = views.api_founder_exam_timetable_source(request, 'test1')
            with self.subTest(filename=filename):
                self.assertEqual(response.status_code, 400)
                self.assertFalse(json.loads(response.content)['success'])

    def test_oversized_upload_is_rejected_without_reading_its_content(self):
        request = self.factory.post('/api/founder/exam-timetable-source/test1/')
        request.user = self.staff
        oversized = SimpleNamespace(name='large.xlsx', size=25 * 1024 * 1024 + 1, read=Mock())
        request.FILES['file'] = oversized
        with patch.object(views, 'schema_context', return_value=nullcontext()):
            response = views.api_founder_exam_timetable_source(request, 'test1')
        self.assertEqual(response.status_code, 413)
        oversized.read.assert_not_called()

    def test_xlsx_upload_advances_the_selected_source_version(self):
        workbook = make_exam_workbook([('BAE', [
            ['Date', 'Time', 'Course Code', 'Course Name'],
            ['2026-12-01', '09:00 - 11:00', 'AEU 08101', 'Automobile Design'],
        ])])
        request = self.factory.post('/api/founder/exam-timetable-source/test2/')
        request.user = self.staff
        request.FILES['file'] = SimpleUploadedFile('test2.xlsx', workbook)
        source = SimpleNamespace(
            type='test2', file=SimpleNamespace(name='safe.xlsx'), original_filename='test2.xlsx',
            version=2, uploaded_at=None,
        )
        metadata = {
            key: {'type': key, 'filename': None, 'version': 0, 'uploaded_at': None, 'has_file': False}
            for key in ('test1', 'test2', 'ue')
        }
        metadata['test2'].update(filename='test2.xlsx', version=2, has_file=True)
        with patch.object(views, 'schema_context', return_value=nullcontext()), patch.object(
            views, '_store_new_source_file', return_value=source
        ), patch.object(views, 'exam_sources_metadata', return_value=metadata) as sources:
            response = views.api_founder_exam_timetable_source(request, 'test2')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['data']['version'], 2)
        self.assertEqual(json.loads(response.content)['data']['type'], 'test2')
        sources.assert_called_once()

    def test_pdf_endpoint_uses_saved_tenant_cohort_and_returns_pdf(self):
        workbook = make_exam_workbook([('Examinations', [
            ['Programme', 'Date', 'Time', 'Course Code', 'Course Name', 'Venue'],
            ['BAE (65)', '2026-12-01', '09:00 - 11:00', 'AEU 08101', 'Automobile Design', 'Hall A'],
        ])])
        tenant_state_manager = Mock()
        tenant_state_manager.filter.return_value.values.return_value.first.return_value = {
            'state': {'cohort': 'BAE (65)'}
        }
        file_obj = SimpleNamespace(open=Mock(return_value=__import__('contextlib').nullcontext(BytesIO(workbook))))
        source = SimpleNamespace(file=file_obj, version=3)
        source_manager = Mock()
        source_manager.filter.return_value.first.return_value = source
        request = self.factory.get('/api/timetable/exam-pdf/test1/')
        request.tenant = SimpleNamespace(schema_name='tenant_a')
        with patch.object(views, 'schema_context', return_value=nullcontext()), patch.object(
            views.TenantTimetableState, 'objects', tenant_state_manager
        ), patch.object(FounderExamTimetableSource, 'objects', source_manager), patch.object(
            views.cache, 'get', return_value=None
        ), patch.object(views.cache, 'set'):
            response = views.api_tenant_exam_timetable_pdf(request, 'test1')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF-'))
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('BAE_TEST1_v3.pdf', response['Content-Disposition'])
        tenant_state_manager.filter.assert_called_once_with(pk=1)

    def test_pdf_without_saved_cohort_returns_400_without_source_lookup(self):
        tenant_state_manager = Mock()
        tenant_state_manager.filter.return_value.values.return_value.first.return_value = {'state': {}}
        source_manager = Mock()
        request = self.factory.get('/api/timetable/exam-pdf/test1/')
        request.tenant = SimpleNamespace(schema_name='tenant_a')
        with patch.object(views, 'schema_context', return_value=nullcontext()), patch.object(
            views.TenantTimetableState, 'objects', tenant_state_manager
        ), patch.object(FounderExamTimetableSource, 'objects', source_manager):
            response = views.api_tenant_exam_timetable_pdf(request, 'test1')
        self.assertEqual(response.status_code, 400)
        source_manager.filter.assert_not_called()

    def test_pdf_requires_a_tenant_and_rejects_unknown_type(self):
        request = self.factory.get('/api/timetable/exam-pdf/test1/')
        request.tenant = None
        self.assertEqual(views.api_tenant_exam_timetable_pdf(request, 'test1').status_code, 400)
        request.tenant = SimpleNamespace(schema_name='tenant_a')
        self.assertEqual(views.api_tenant_exam_timetable_pdf(request, 'final').status_code, 404)
