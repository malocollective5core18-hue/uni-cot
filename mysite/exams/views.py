import logging
import re
from pathlib import Path
from uuid import uuid4

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import DatabaseError, transaction
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_tenants.utils import schema_context

from core.models import TenantTimetableState
from .models import FounderExamTimetableSource
from .services import (
    ExamCohortAmbiguous,
    ExamCohortNotFound,
    ExamGrid,
    ExamGridCell,
    ExamGridDay,
    ExamTimetableError,
    build_exam_grid_pdf,
    build_exam_pdf,
    exam_sources_metadata,
    extract_exam_timetable,
    inspect_exam_workbook,
    normalize_cohort,
)


logger = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
EXAM_TYPES = {key for key, _label in FounderExamTimetableSource.TYPE_CHOICES}
EXAM_LABELS = dict(FounderExamTimetableSource.TYPE_CHOICES)


def _demo_test1_timetable():
    """Return the fixed BAE (65) Test 1 sample, independent of tenant storage."""
    times = (
        '07:00-08:00', '08:00-08:30', '08:30-09:30', '09:30-10:00',
        '10:00-11:00', '11:00-11:30', '11:30-12:30', '12:30-13:00',
        '13:00-14:00', '14:00-14:30', '14:30-15:30', '15:30-16:00',
        '16:00-17:00', '17:00-17:30', '17:30-18:30',
    )
    empty = [ExamGridCell() for _ in times]

    def sample_day(label, exams=()):
        cells = list(empty)
        for index, course, venue in exams:
            cells[index] = ExamGridCell(course=course, venue=venue)
        return ExamGridDay(label, tuple(cells))

    return ExamGrid(
        title='NATIONAL INSTITUTE OF TRANSPORT',
        cohort='BAE (65)',
        time_slots=times,
        days=(
            sample_day('Monday 05/01/2026', (
                (6, 'AEU 08102 Engine Management', 'LR 02&03 BL 14 (72)+LR 04 B14 (30)'),
                (8, 'AEU 08103 Engineering Management', 'LR 02&03 BL 14 (72)+LR 04 B14 (30)'),
            )),
            sample_day('Tuesday 06/01/2026', (
                (2, 'GSU 08106T Law for Engineers', 'LR 02&03 BL 14 (72)+LR 04 B14 (30)'),
                (6, 'AEU 08104 Environmental & Safety Engineering', 'LR 02&03 BL 14 (72)+LR 04 B14 (30)'),
            )),
            sample_day('Wednesday 07/01/2026'),
            sample_day('Thursday 08/01/2026'),
            sample_day('Friday 09/01/2026', (
                (14, 'AEU 08101 Automobile Design & Development', 'LR 02&03 BL 14 (72)+LR 04 B14 (30)'),
            )),
            sample_day('Saturday 10/01/2026'),
            sample_day('Sunday 11/01/2026'),
            sample_day('Monday 12/01/2026'),
            sample_day('Tuesday 13/01/2026'),
        ),
        subtitle='FIRST SEMESTER &nbsp;&nbsp;&nbsp; TEST I &nbsp;&nbsp;&nbsp; YEAR 2025/2026',
    )


@require_http_methods(['GET'])
def api_demo_exam_timetable_pdf(request, exam_type):
    """Return the fixed BAE (65) sample without tenant or database reads."""
    if exam_type != 'test1':
        return JsonResponse({'success': False, 'error': 'Only the Test 01 demo preview is available.'}, status=404)
    try:
        pdf_bytes = build_exam_grid_pdf(_demo_test1_timetable(), 'test1', 'SAMPLE PREVIEW')
    except Exception:
        logger.exception('Could not render the public Test 01 sample PDF')
        return JsonResponse({'success': False, 'error': 'The Test 01 sample PDF is temporarily unavailable.'}, status=503)
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="DEMO_TEST01_BAE65_SAMPLE.pdf"'
    response['Cache-Control'] = 'public, max-age=3600'
    return response


def _delete_storage_file(storage, name):
    if not name:
        return
    try:
        storage.delete(name)
    except Exception:
        logger.exception('Failed to remove a superseded examination workbook from storage')


def _validate_upload(upload):
    if not upload:
        raise ExamTimetableError('Choose an .xlsx examination timetable.')
    filename = Path((upload.name or '').replace('\\', '/')).name
    if Path(filename).suffix.lower() != '.xlsx':
        raise ExamTimetableError('Only .xlsx workbooks are supported. Legacy .xls files are not supported.')
    if upload.size > MAX_UPLOAD_BYTES:
        raise ExamTimetableError('Examination workbooks must be 25 MB or smaller.')
    contents = upload.read(MAX_UPLOAD_BYTES + 1)
    if len(contents) > MAX_UPLOAD_BYTES:
        raise ExamTimetableError('Examination workbooks must be 25 MB or smaller.')
    if not contents.startswith(b'PK\x03\x04'):
        raise ExamTimetableError('The uploaded file is not a valid .xlsx workbook.')
    inspect_exam_workbook(contents)
    return filename, contents


def _store_new_source_file(source_type, filename, contents, uploaded_by_id):
    field = FounderExamTimetableSource._meta.get_field('file')
    storage = field.storage
    path = f'system/exam_timetables/{source_type}-{uuid4().hex}.xlsx'
    stored_name = storage.save(path, ContentFile(contents))
    old_name = None
    try:
        with transaction.atomic():
            source, _created = FounderExamTimetableSource.objects.select_for_update().get_or_create(
                type=source_type,
            )
            old_name = source.file.name if source.file else None
            source.file.name = stored_name
            source.original_filename = filename[:255]
            source.version += 1
            source.uploaded_by_id = uploaded_by_id
            source.uploaded_at = timezone.now()
            source.save(update_fields=('file', 'original_filename', 'version', 'uploaded_by_id', 'uploaded_at'))
        transaction.on_commit(lambda: _delete_storage_file(storage, old_name))
    except Exception:
        _delete_storage_file(storage, stored_name)
        raise
    return source


def _delete_source(source_type):
    with transaction.atomic():
        source = FounderExamTimetableSource.objects.select_for_update().filter(type=source_type).first()
        if not source:
            return None
        old_name = source.file.name if source.file else None
        if not old_name:
            return source
        storage = source.file.storage
        source.file = ''
        source.original_filename = ''
        source.uploaded_by_id = None
        source.version += 1
        source.uploaded_at = timezone.now()
        source.save(update_fields=('file', 'original_filename', 'version', 'uploaded_by_id', 'uploaded_at'))
    transaction.on_commit(lambda: _delete_storage_file(storage, old_name))
    return source


@require_http_methods(['GET', 'POST', 'DELETE'])
def api_founder_exam_timetable_source(request, exam_type):
    if exam_type not in EXAM_TYPES:
        return JsonResponse({'success': False, 'error': 'Unknown examination timetable type.'}, status=404)
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or not user.is_active or not user.is_staff:
        return JsonResponse({'success': False, 'error': 'Founder access required.'}, status=403)

    try:
        with schema_context('public'):
            if request.method == 'GET':
                return JsonResponse({'success': True, 'data': exam_sources_metadata()[exam_type]})
            if request.method == 'POST':
                filename, contents = _validate_upload(request.FILES.get('file'))
                _store_new_source_file(exam_type, filename, contents, user.id)
                return JsonResponse({'success': True, 'data': exam_sources_metadata()[exam_type]})
            _delete_source(exam_type)
            return JsonResponse({'success': True, 'data': exam_sources_metadata()[exam_type]})
    except ExamTimetableError as exc:
        status = 413 if '25 MB' in str(exc) else 400
        return JsonResponse({'success': False, 'error': str(exc)}, status=status)
    except DatabaseError:
        logger.exception('Examination timetable source update failed for type=%s', exam_type)
        return JsonResponse({'success': False, 'error': 'The examination timetable source could not be updated.'}, status=503)
    except Exception:
        logger.exception('Examination timetable storage failed for type=%s', exam_type)
        return JsonResponse({'success': False, 'error': 'The examination timetable source could not be updated.'}, status=503)


def _safe_download_name(cohort, exam_type, version):
    parts = [normalize_cohort(cohort).replace(' ', '_'), exam_type.upper(), f'v{version}']
    stem = re.sub(r'[^A-Za-z0-9_-]+', '_', '_'.join(parts)).strip('_')[:120] or f'{exam_type.upper()}_TIMETABLE'
    return f'{stem}.pdf'


@require_http_methods(['GET'])
def api_tenant_exam_timetable_pdf(request, exam_type):
    if exam_type not in EXAM_TYPES:
        return JsonResponse({'success': False, 'error': 'Unknown examination timetable type.'}, status=404)
    tenant = getattr(request, 'tenant', None)
    schema_name = getattr(tenant, 'schema_name', None)
    if not schema_name or schema_name in {'public', ''}:
        return JsonResponse({'success': False, 'error': 'A tenant timetable is required.'}, status=400)

    try:
        with schema_context(schema_name):
            tenant_state = TenantTimetableState.objects.filter(pk=1).values('state').first()
        state = tenant_state.get('state') if tenant_state else None
        cohort = state.get('cohort') if isinstance(state, dict) else None
        if not isinstance(cohort, str) or not normalize_cohort(cohort):
            return JsonResponse({'success': False, 'error': 'This tenant has not saved a timetable cohort.'}, status=400)

        with schema_context('public'):
            source = FounderExamTimetableSource.objects.filter(type=exam_type).first()
            if not source or not source.file:
                return JsonResponse({'success': False, 'error': f'No {EXAM_LABELS[exam_type]} timetable has been uploaded.'}, status=404)
            version = source.version
            try:
                with source.file.open('rb') as stored_file:
                    workbook_bytes = stored_file.read(MAX_UPLOAD_BYTES + 1)
            except Exception:
                logger.exception('Could not read examination source type=%s version=%s', exam_type, version)
                return JsonResponse({'success': False, 'error': 'The examination timetable file is unavailable.'}, status=503)

        if len(workbook_bytes) > MAX_UPLOAD_BYTES:
            return JsonResponse({'success': False, 'error': 'The examination timetable file is too large to process.'}, status=422)
        cache_key = f'exam-pdf:{exam_type}:{version}:{normalize_cohort(cohort)}'
        try:
            cached_pdf = cache.get(cache_key)
        except Exception:
            cached_pdf = None
        if cached_pdf:
            pdf_bytes = cached_pdf
        else:
            try:
                timetable = extract_exam_timetable(workbook_bytes, cohort)
                if isinstance(timetable, ExamGrid):
                    pdf_bytes = build_exam_grid_pdf(timetable, exam_type, version)
                else:
                    pdf_bytes = build_exam_pdf(timetable, cohort, exam_type, version)
            except ExamCohortNotFound as exc:
                return JsonResponse({'success': False, 'error': str(exc)}, status=404)
            except ExamCohortAmbiguous as exc:
                logger.warning('Ambiguous exam cohort match type=%s cohort=%r: %s', exam_type, cohort, exc)
                return JsonResponse({'success': False, 'error': str(exc)}, status=409)
            except ExamTimetableError as exc:
                return JsonResponse({'success': False, 'error': str(exc)}, status=422)
            except Exception:
                logger.exception('Failed to parse or render examination PDF type=%s version=%s', exam_type, version)
                return JsonResponse({'success': False, 'error': 'The examination timetable could not be processed.'}, status=422)
            try:
                cache.set(cache_key, pdf_bytes, timeout=300)
            except Exception:
                logger.warning('Could not cache generated exam PDF type=%s version=%s', exam_type, version)
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{_safe_download_name(cohort, exam_type, version)}"'
        response['Cache-Control'] = 'private, no-store'
        response['X-Exam-Source-Version'] = str(version)
        return response
    except DatabaseError:
        logger.exception('Examination PDF request failed for tenant schema=%s type=%s', schema_name, exam_type)
        return JsonResponse({'success': False, 'error': 'The examination timetable is temporarily unavailable.'}, status=503)
