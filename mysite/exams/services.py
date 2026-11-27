"""Strict, reusable examination workbook parsing and PDF rendering."""

from dataclasses import dataclass
from datetime import date, datetime, time
from io import BytesIO
import re
import unicodedata
from zipfile import BadZipFile, ZipFile

from django.utils import timezone
from openpyxl import load_workbook

from .models import FounderExamTimetableSource


class ExamTimetableError(ValueError):
    """A safe, user-facing examination timetable validation error."""


class ExamCohortNotFound(ExamTimetableError):
    pass


class ExamCohortAmbiguous(ExamTimetableError):
    pass


@dataclass(frozen=True)
class ExamRecord:
    exam_date: date
    day: str
    start_time: time
    end_time: time
    course_code: str
    course_name: str
    venue: str


@dataclass(frozen=True)
class ExamGridCell:
    course: str = ''
    venue: str = ''
    blocked: bool = False


@dataclass(frozen=True)
class ExamGridDay:
    label: str
    cells: tuple[ExamGridCell, ...]


@dataclass(frozen=True)
class ExamGrid:
    title: str
    cohort: str
    time_slots: tuple[str, ...]
    days: tuple[ExamGridDay, ...]
    subtitle: str = ''


HEADER_ALIASES = {
    'cohort': {'cohort', 'cohort code', 'class', 'class group', 'programme', 'program', 'programme group', 'program group', 'student group'},
    'date': {'date', 'exam date', 'date of exam', 'examination date', 'date of examination', 'paper date'},
    'day': {'day', 'exam day', 'examination day'},
    'time': {'time', 'exam time', 'time of exam', 'examination time', 'time slot'},
    'start': {'start', 'start time', 'exam start', 'from'},
    'end': {'end', 'end time', 'exam end', 'to'},
    'code': {'code', 'course code', 'subject code', 'module code', 'paper code'},
    'name': {'course', 'course name', 'subject name', 'module name', 'paper', 'course title', 'subject'},
    'venue': {'venue', 'room', 'room venue', 'exam venue', 'examination venue', 'hall'},
}
SHEET_NOISE = re.compile(r'\b(TEST\s*[12]|UE|EXAMINATIONS?|TIMETABLE|SCHEDULE)\b', re.I)
TIME_RANGE = re.compile(
    r'^\s*(\d{1,2}(?::\d{2})?\s*(?:AM|PM)?)\s*(?:-|–|—|\bto\b)\s*'
    r'(\d{1,2}(?::\d{2})?\s*(?:AM|PM)?)\s*$', re.I,
)


def exam_sources_metadata():
    sources = {source.type: source for source in FounderExamTimetableSource.objects.all()}
    result = {}
    for source_type, _label in FounderExamTimetableSource.TYPE_CHOICES:
        source = sources.get(source_type)
        has_file = bool(source and source.file and source.file.name)
        result[source_type] = {
            'type': source_type,
            'filename': source.original_filename if has_file else None,
            'version': source.version if source else 0,
            'uploaded_at': source.uploaded_at.isoformat() if source and source.uploaded_at else None,
            'has_file': has_file,
        }
    return result


def normalize_cohort(value):
    """Normalize case, punctuation, whitespace, and a trailing student count."""
    if value is None:
        return ''
    value = unicodedata.normalize('NFKC', str(value)).strip().upper()
    value = re.sub(r'\s*\(\s*\d+\s*\)\s*$', '', value)
    value = re.sub(r'(?<=[A-Z])\.(?=[A-Z])', '', value)
    value = re.sub(r'[^A-Z0-9]+', ' ', value)
    return ' '.join(value.split())


def _cell_text(value):
    if value is None:
        return ''
    return ' '.join(str(value).replace('\u00a0', ' ').split())


def _header_field(value):
    text = normalize_cohort(value)
    for field, aliases in HEADER_ALIASES.items():
        if text in {normalize_cohort(alias) for alias in aliases}:
            return field
    return None


def _load_exam_workbook(file_bytes, *, read_only=False):
    try:
        with ZipFile(BytesIO(file_bytes)) as archive:
            names = set(archive.namelist())
            if 'xl/workbook.xml' not in names or '[Content_Types].xml' not in names:
                raise ExamTimetableError('Upload a valid .xlsx examination workbook.')
            if sum(item.file_size for item in archive.infolist()) > 120 * 1024 * 1024:
                raise ExamTimetableError('The expanded workbook is too large to process safely.')
        workbook = load_workbook(BytesIO(file_bytes), read_only=read_only, data_only=True)
    except ExamTimetableError:
        raise
    except (BadZipFile, OSError, ValueError, KeyError) as exc:
        raise ExamTimetableError('The uploaded .xlsx workbook is damaged or unsupported.') from exc
    if not workbook.worksheets:
        raise ExamTimetableError('The workbook does not contain a worksheet.')
    return workbook


def inspect_exam_workbook(file_bytes):
    """Reject workbooks without both an exam table and a supported cohort key."""
    if _find_grid_sections(file_bytes):
        return
    workbook = _load_exam_workbook(file_bytes)
    supported_table = any(
        'cohort' in columns or _sheet_cohort(sheet.title)
        for sheet in workbook.worksheets
        for _header_index, columns in _find_header_rows(sheet)
    )
    if not supported_table:
        raise ExamTimetableError(
            'No supported examination table and cohort key were found. Include columns for cohort, date, '
            'course code or course name, and exam time, or name each worksheet for its cohort.'
        )


def _grid_times(row):
    """Return a full row of time labels only when it looks like a timetable header."""
    times = tuple(_cell_text(value) for value in row[1:])
    if sum(bool(TIME_RANGE.match(value)) for value in times) < 4:
        return ()
    return times


def _is_exam_day(value):
    return bool(re.match(
        r'^(?:MONDAY|TUESDAY|WEDNESDAY|THURSDAY|FRIDAY|SATURDAY|SUNDAY|'
        r'MON|TUE|WED|THU|FRI|SAT|SUN)\b',
        _cell_text(value), re.I,
    ))


def _find_grid_sections(file_bytes):
    """Read timetable-shaped workbooks without expanding their many Excel merges."""
    workbook = _load_exam_workbook(file_bytes, read_only=True)
    sections = []
    try:
        for sheet in workbook.worksheets:
            rows = list(sheet.iter_rows(values_only=True))
            headers = [(index, _grid_times(row)) for index, row in enumerate(rows)]
            headers = [(index, times) for index, times in headers if times]
            for header_position, (header_index, columns) in enumerate(headers):
                if header_index < 1:
                    continue
                cohort_row = rows[header_index - 1]
                cohort = _cell_text(cohort_row[1] if len(cohort_row) > 1 else '')
                if not cohort:
                    cohort = _sheet_cohort(sheet.title)
                    if re.fullmatch(r'SHEET\s*\d*', cohort):
                        cohort = ''
                if not normalize_cohort(cohort):
                    continue
                title = ''
                if header_index >= 2:
                    title_row = rows[header_index - 2]
                    title = _cell_text(title_row[0] if title_row else '')
                end_index = headers[header_position + 1][0] if header_position + 1 < len(headers) else len(rows)
                days = []
                row_index = header_index + 1
                while row_index < end_index:
                    row = rows[row_index]
                    day_label = _cell_text(row[0] if row else '')
                    if not _is_exam_day(day_label):
                        row_index += 1
                        continue
                    detail_row = rows[row_index + 1] if row_index + 1 < end_index else ()
                    if detail_row and _is_exam_day(detail_row[0] if detail_row else ''):
                        detail_row = ()
                    cells = []
                    for column_index in range(len(columns)):
                        source_column = column_index + 1
                        value = _cell_text(row[source_column] if source_column < len(row) else '')
                        upper_value = value.upper()
                        blocked = upper_value in {'-X-', 'X'}
                        course = '' if not value or upper_value in {'---', '-X-', 'X'} else value
                        venue_value = _cell_text(
                            detail_row[source_column] if source_column < len(detail_row) else ''
                        )
                        cells.append(ExamGridCell(course=course, venue=venue_value if course else '', blocked=blocked))
                    days.append(ExamGridDay(day_label, tuple(cells)))
                    row_index += 2 if detail_row else 1
                if days and any(cell.course for day in days for cell in day.cells):
                    sections.append({
                        'cohort': cohort,
                        'title': title,
                        'time_slots': columns,
                        'days': tuple(days),
                    })
    finally:
        workbook.close()
    return sections


def extract_exam_timetable(file_bytes, cohort):
    """Extract either the workbook's timetable grid or its row-oriented exam table."""
    sections = _find_grid_sections(file_bytes)
    if sections:
        selected_label = _resolve_target(cohort, [section['cohort'] for section in sections])
        matches = [section for section in sections if normalize_cohort(section['cohort']) == selected_label]
        if len(matches) != 1:
            raise ExamCohortAmbiguous(
                f'The cohort {cohort} appears in multiple examination timetable sections.'
            )
        section = matches[0]
        return ExamGrid(
            title=section['title'], cohort=section['cohort'],
            time_slots=section['time_slots'], days=section['days'],
        )
    return extract_cohort_records(file_bytes, cohort)


def _expanded_rows(sheet):
    rows = [[cell.value for cell in row] for row in sheet.iter_rows()]
    for merged in sheet.merged_cells.ranges:
        value = sheet.cell(merged.min_row, merged.min_col).value
        for row_index in range(merged.min_row - 1, merged.max_row):
            for col_index in range(merged.min_col - 1, merged.max_col):
                while len(rows) <= row_index:
                    rows.append([])
                while len(rows[row_index]) <= col_index:
                    rows[row_index].append(None)
                rows[row_index][col_index] = value
    return rows


def _find_header_rows(sheet):
    result = []
    for row_index, row in enumerate(_expanded_rows(sheet)):
        columns = {}
        for column_index, value in enumerate(row):
            field = _header_field(value)
            if field and field not in columns:
                columns[field] = column_index
        if 'date' in columns and ('code' in columns or 'name' in columns) and (
            'time' in columns or ('start' in columns and 'end' in columns)
        ):
            result.append((row_index, columns))
    return result


def _sheet_cohort(sheet_title):
    title = SHEET_NOISE.sub(' ', unicodedata.normalize('NFKC', sheet_title))
    return normalize_cohort(title)


def _resolve_target(cohort, candidates):
    target = normalize_cohort(cohort)
    if not target:
        raise ExamTimetableError('This tenant has no saved cohort in its class timetable.')
    labels = sorted({normalize_cohort(value) for value in candidates if normalize_cohort(value)})
    exact = [label for label in labels if label == target]
    if exact:
        return exact[0]
    related_sections = [label for label in labels if label.startswith(target + ' ')]
    if len(target.split()) == 1:
        related_sections.extend(
            label for label in labels
            if label.split()[0] == target and len(label.split()) > 1 and label not in related_sections
        )
    if len(related_sections) > 1:
        raise ExamCohortAmbiguous(
            f'The cohort {cohort} matches multiple examination sections: {", ".join(related_sections)}.'
        )
    raise ExamCohortNotFound(f'No examination timetable section matches cohort {cohort}.')


def _parse_date(value, row_number):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _cell_text(value)
    for pattern in ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d-%b-%Y', '%d %B %Y', '%d %b %Y'):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    raise ExamTimetableError(f'Row {row_number} has an invalid or missing examination date.')


def _parse_time(value, row_number):
    if isinstance(value, datetime):
        return value.time().replace(second=0, microsecond=0)
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    text = _cell_text(value).upper().replace('.', ':')
    for pattern in ('%H:%M', '%H%M', '%I:%M %p', '%I %p', '%I:%M%p'):
        try:
            return datetime.strptime(text, pattern).time()
        except ValueError:
            continue
    raise ExamTimetableError(f'Row {row_number} has an invalid examination time.')


def _time_values(row, columns, row_number):
    if 'start' in columns and 'end' in columns:
        return (
            _parse_time(row[columns['start']] if columns['start'] < len(row) else None, row_number),
            _parse_time(row[columns['end']] if columns['end'] < len(row) else None, row_number),
        )
    raw = row[columns['time']] if columns['time'] < len(row) else None
    if isinstance(raw, (time, datetime)):
        start = _parse_time(raw, row_number)
        return start, start
    match = TIME_RANGE.match(_cell_text(raw))
    if not match:
        raise ExamTimetableError(f'Row {row_number} must contain a start and end exam time.')
    return _parse_time(match.group(1), row_number), _parse_time(match.group(2), row_number)


def extract_cohort_records(file_bytes, cohort):
    """Extract strict row-oriented tables with a cohort column or cohort sheet."""
    workbook = _load_exam_workbook(file_bytes)
    tables = [(sheet, _expanded_rows(sheet), _find_header_rows(sheet)) for sheet in workbook.worksheets]
    candidates = []
    for sheet, rows, headers in tables:
        candidates.append(_sheet_cohort(sheet.title))
        for _header_row, columns in headers:
            if 'cohort' in columns:
                candidates.extend(
                    row[columns['cohort']]
                    for row in rows
                    if columns['cohort'] < len(row) and _cell_text(row[columns['cohort']])
                )
    selected = _resolve_target(cohort, candidates)
    records = []
    for sheet, rows, headers in tables:
        title_match = _sheet_cohort(sheet.title) == selected
        for header_index, columns in headers:
            row_cohort = None
            if 'cohort' not in columns and not title_match:
                continue
            for row_index in range(header_index + 1, len(rows)):
                row = rows[row_index]
                if _row_repeats_header(row, columns):
                    continue
                if 'cohort' in columns:
                    raw_cohort = row[columns['cohort']] if columns['cohort'] < len(row) else None
                    if _cell_text(raw_cohort):
                        row_cohort = normalize_cohort(raw_cohort)
                    if row_cohort != selected:
                        continue
                values = [row[index] if index < len(row) else None for index in columns.values()]
                if not any(_cell_text(value) for value in values):
                    continue
                code = _column_text(row, columns, 'code')
                name = _column_text(row, columns, 'name')
                if not code and not name:
                    raise ExamTimetableError(
                        f'{sheet.title}, row {row_index + 1} has timetable data but no course code or name.'
                    )
                date_value = row[columns['date']] if columns['date'] < len(row) else None
                exam_date = _parse_date(date_value, row_index + 1)
                start_time, end_time = _time_values(row, columns, row_index + 1)
                if end_time < start_time:
                    raise ExamTimetableError(f'{sheet.title}, row {row_index + 1} ends before it starts.')
                day_value = _column_text(row, columns, 'day')
                records.append(ExamRecord(
                    exam_date=exam_date,
                    day=day_value or exam_date.strftime('%A'),
                    start_time=start_time,
                    end_time=end_time,
                    course_code=code,
                    course_name=name,
                    venue=_column_text(row, columns, 'venue'),
                ))
    if not records:
        if candidates:
            raise ExamTimetableError(f'No examination records were found for cohort {cohort}.')
        raise ExamTimetableError('No supported examination timetable layout was found.')
    return sorted(records, key=lambda item: (item.exam_date, item.start_time, item.course_code, item.course_name))


def _row_repeats_header(row, columns):
    return sum(
        1 for field, index in columns.items()
        if index < len(row) and _header_field(row[index]) == field
    ) >= 2


def _column_text(row, columns, field):
    index = columns.get(field)
    return _cell_text(row[index]) if index is not None and index < len(row) else ''


def build_exam_pdf(records, cohort, exam_type, version):
    """Render a paginated, portrait A4 PDF with a repeated table header."""
    if not records:
        raise ExamTimetableError('No examination records are available for this cohort.')
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import LongTable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, TableStyle
    except ImportError as exc:
        raise RuntimeError('PDF generation is unavailable because ReportLab is not installed.') from exc

    exam_labels = {'test1': 'Test 1', 'test2': 'Test 2', 'ue': 'UE'}
    page_size = A4
    output = BytesIO()
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('ExamTitle', parent=styles['Title'], fontName='Helvetica-Bold', fontSize=17, leading=20, textColor=colors.HexColor('#123047'), alignment=TA_LEFT, spaceAfter=3)
    subtitle_style = ParagraphStyle('ExamSubtitle', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=12, textColor=colors.HexColor('#496276'))
    cell_style = ParagraphStyle('ExamCell', parent=styles['Normal'], fontName='Helvetica', fontSize=7.5, leading=9, wordWrap='CJK')
    header_style = ParagraphStyle('ExamHeader', parent=cell_style, fontName='Helvetica-Bold', textColor=colors.white)
    document = SimpleDocTemplate(
        output, pagesize=page_size, leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=14 * mm, bottomMargin=13 * mm,
        title=f'{cohort} {exam_labels[exam_type]} examination timetable',
        author='UNI-COT',
    )
    story = [
        Paragraph('UNI-COT', ParagraphStyle('Brand', parent=title_style, fontSize=10, leading=12, textColor=colors.HexColor('#0f766e'))),
        Paragraph(f'{exam_labels[exam_type]} Examination Timetable', title_style),
        Paragraph(f'Cohort: {_escape_pdf(cohort)} &nbsp;&nbsp; | &nbsp;&nbsp; Source version: {version} &nbsp;&nbsp; | &nbsp;&nbsp; Generated: {timezone.localdate():%d %B %Y}', subtitle_style),
        Spacer(1, 7 * mm),
    ]
    headers = ['Date', 'Day', 'Time', 'Course code', 'Course name', 'Venue']
    data = [[Paragraph(value, header_style) for value in headers]]
    for record in records:
        values = [
            record.exam_date.strftime('%d %b %Y'),
            record.day,
            f'{record.start_time:%H:%M}' + (f' - {record.end_time:%H:%M}' if record.end_time != record.start_time else ''),
            record.course_code,
            record.course_name,
            record.venue,
        ]
        data.append([Paragraph(_escape_pdf(value), cell_style) for value in values])
    widths = [18 * mm, 15 * mm, 24 * mm, 28 * mm, 54 * mm, 47 * mm]
    table = LongTable(data, colWidths=widths, repeatRows=1, hAlign='LEFT')
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#123047')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#cbd5e1')),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f1f7f8')]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    story.append(table)

    def draw_page(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#64748b'))
        canvas.drawRightString(page_size[0] - 12 * mm, 7 * mm, f'Page {doc.page}')
        canvas.restoreState()

    document.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return output.getvalue()


def build_exam_grid_pdf(timetable, exam_type, version):
    """Render the complete compact timetable grid on landscape A4 pages."""
    if not timetable.days or not timetable.time_slots:
        raise ExamTimetableError('No examination timetable grid is available for this cohort.')
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle
    except ImportError as exc:
        raise RuntimeError('PDF generation is unavailable because ReportLab is not installed.') from exc

    exam_labels = {'test1': 'Test 1', 'test2': 'Test 2', 'ue': 'UE'}
    label = exam_labels.get(exam_type, 'Examination')
    visible_slots = tuple(range(len(timetable.time_slots)))
    cell_font_size = 6
    page_size = landscape(A4)
    output = BytesIO()
    margin = 5 * mm
    document = SimpleDocTemplate(
        output, pagesize=page_size, leftMargin=margin, rightMargin=margin,
        topMargin=margin, bottomMargin=margin,
        title=f'{timetable.cohort} {label} examination timetable', author='UNI-COT',
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        'ExamGridTitle', parent=styles['Title'], fontName='Helvetica-Bold',
        fontSize=11.5, leading=13, textColor=colors.HexColor('#123047'),
        alignment=TA_LEFT, spaceAfter=2,
    )
    sub_style = ParagraphStyle(
        'ExamGridSubtitle', parent=styles['Normal'], fontName='Helvetica',
        fontSize=7, leading=8, textColor=colors.HexColor('#334155'), spaceAfter=2,
    )
    sample_heading_style = ParagraphStyle(
        'ExamGridSampleHeading', parent=sub_style, fontName='Helvetica-Bold',
        fontSize=8, leading=9, textColor=colors.HexColor('#164e63'), spaceAfter=2,
    )
    header_style = ParagraphStyle(
        'ExamGridHeader', parent=styles['Normal'], fontName='Helvetica-Bold',
        fontSize=cell_font_size, leading=cell_font_size + 1, textColor=colors.HexColor('#123047'), alignment=TA_LEFT,
    )
    day_style = ParagraphStyle(
        'ExamGridDay', parent=header_style, fontSize=cell_font_size + 0.5, leading=cell_font_size + 1.5,
    )
    cell_style = ParagraphStyle(
        'ExamGridCell', parent=styles['Normal'], fontName='Helvetica',
        fontSize=cell_font_size, leading=cell_font_size + 1.4, textColor=colors.HexColor('#10232e'), wordWrap='CJK',
    )

    version_label = 'Sample preview' if version == 'SAMPLE PREVIEW' else f'Source version: {version}'
    content_width = page_size[0] - document.leftMargin - document.rightMargin
    day_width = 18 * mm
    story = []
    office_title, office_subtitle = _office_exam_heading(timetable.title, exam_type)

    if office_title:
        story.append(Paragraph(_escape_pdf(office_title), title_style))
    else:
        story.append(Paragraph(f'{label} Examination Timetable', title_style))
    subtitle = timetable.subtitle or office_subtitle
    if subtitle:
        story.append(Paragraph(subtitle, sample_heading_style))
    story.append(Paragraph(
        f'{label} &nbsp; • &nbsp; Cohort: {_escape_pdf(timetable.cohort)}'
        f' &nbsp; • &nbsp; {_escape_pdf(version_label)}', sub_style,
    ))
    table_data = [[Paragraph('DAY / DATE', header_style)]]
    for slot_index in visible_slots:
        slot = timetable.time_slots[slot_index]
        start_end = TIME_RANGE.match(slot)
        display = f'{start_end.group(1)}<br/>{start_end.group(2)}' if start_end else _escape_pdf(slot)
        table_data[0].append(Paragraph(display, header_style))
    for day in timetable.days:
        row = [Paragraph(_compact_day_label(day.label), day_style)]
        for slot_index in visible_slots:
            cell = day.cells[slot_index] if slot_index < len(day.cells) else ExamGridCell()
            if cell.course:
                text = f'<b>{_escape_pdf(cell.course)}</b>'
                if cell.venue:
                    text += f'<br/>{_escape_pdf(cell.venue)}'
                row.append(Paragraph(text, cell_style))
            else:
                row.append('')
        table_data.append(row)

    durations = [_time_slot_minutes(timetable.time_slots[index]) for index in visible_slots]
    duration_total = sum(durations) or len(durations)
    slot_widths = [(content_width - day_width) * duration / duration_total for duration in durations]
    table = LongTable(
        table_data, colWidths=[day_width, *slot_widths],
        repeatRows=1,
        hAlign='LEFT', splitByRow=1,
    )
    commands = [
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e8eef2')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#123047')),
        ('BACKGROUND', (0, 1), (0, -1), colors.HexColor('#edf3f7')),
        ('GRID', (0, 0), (-1, -1), 0.35, colors.HexColor('#a6b4c0')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    for day_index, day in enumerate(timetable.days, start=1):
        for column_index, slot_index in enumerate(visible_slots, start=1):
            cell = day.cells[slot_index] if slot_index < len(day.cells) else ExamGridCell()
            if cell.course:
                commands.append(('BACKGROUND', (column_index, day_index), (column_index, day_index), colors.HexColor('#def4e9')))
                commands.append(('BOX', (column_index, day_index), (column_index, day_index), 0.65, colors.HexColor('#16875a')))
            elif cell.blocked:
                commands.append(('BACKGROUND', (column_index, day_index), (column_index, day_index), colors.HexColor('#e5e7eb')))
    table.setStyle(TableStyle(commands))
    story.extend((Spacer(1, 1 * mm), table))

    def draw_page(canvas, doc):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.setFillColor(colors.HexColor('#64748b'))
        canvas.drawRightString(page_size[0] - margin, 2.5 * mm, f'Page {doc.page}')
        canvas.restoreState()

    document.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return output.getvalue()


def _office_exam_heading(title, exam_type):
    """Split standard source workbook headings into a clean office title and line."""
    source_title = _cell_text(title)
    semester = re.search(r'\b(FIRST|SECOND|THIRD|1ST|2ND|3RD)\s+SEMESTER\b', source_title, re.I)
    year = re.search(r'\b(?:ACADEMIC\s+)?YEAR\s+(\d{4})[_/-](\d{4})\b', source_title, re.I)
    if not semester or not year:
        return source_title, ''

    institution = source_title[:semester.start()].strip(' -:|')
    if not institution:
        return source_title, ''
    test = re.search(r'\bTEST\s*(IV|III|II|I|\d+)\b', source_title, re.I)
    exam_label = f'TEST {test.group(1).upper()}' if test else {'ue': 'UE'}.get(exam_type, 'EXAMINATION')
    semester_label = f'{semester.group(1).upper()} SEMESTER'
    academic_year = f'YEAR {year.group(1)}/{year.group(2)}'
    subtitle = f'{_escape_pdf(semester_label)} &nbsp;&nbsp;&nbsp; {_escape_pdf(exam_label)} &nbsp;&nbsp;&nbsp; {_escape_pdf(academic_year)}'
    return institution, subtitle


def _compact_day_label(value):
    match = re.match(r'^(\w+)\s+(\d{1,2})/(\d{1,2})/(\d{4})$', _cell_text(value))
    if not match:
        return _escape_pdf(value)
    day, date_day, month, year = match.groups()
    return f'{_escape_pdf(day[:3])}<br/>{date_day}/{month}/{year}'


def _time_slot_minutes(value):
    match = TIME_RANGE.match(_cell_text(value))
    if not match:
        return 60
    start = _parse_time(match.group(1), 0)
    end = _parse_time(match.group(2), 0)
    minutes = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
    return minutes if minutes > 0 else 60


def _escape_pdf(value):
    from xml.sax.saxutils import escape

    return escape(str(value or '')).replace('\n', '<br/>')
