# Founder-managed examination timetables

The founder control panel stores Test 1, Test 2, and UE workbooks independently
through the configured Django storage backend. Each source has a retained,
monotonic version, including after deletion. The `exams` app is in
`SHARED_APPS` only; its source table is created in `public` and is not migrated
into tenant schemas. Tenant download requests resolve the cohort from the
current tenant's `TenantTimetableState.state['cohort']`.

The public `/system/` timetable demo has a separate Test 01 download containing
the fixed BAE (65) sample from the supplied Test 1 workbook. The PDF identifies
it as a sample; the route does not read uploaded founder workbooks or tenant
timetable state. Tenant exam downloads continue to use the tenant-specific
endpoint and saved cohort.

## Workbook support

Uploads must be valid `.xlsx` files no larger than 25 MB. Legacy `.xls` is not
supported because the project does not include an `.xls` parser. The parser
accepts row-oriented tables with a cohort column (such as `Programme`, `Class`,
or `Cohort`) and timetable grids. A grid workbook must have a row containing at
least four time ranges, a cohort label directly above it (or a cohort-specific
worksheet name), and dated weekday rows below it. Exam names are read from each
day row and venues from the following row. Every dated row in the selected
cohort section is retained, including schedules that span multiple weeks. Exam
dates come from this exam workbook, independently of the tenant's recurring
class timetable. Grid PDFs keep every source time column, including empty
periods, in the same full-width landscape A4 grid style as the regular class
timetable printout. Scheduled exams are green and unavailable periods are gray.
Dates, courses, venues, and the full source time axis are preserved. Standard workbook
headings are formatted into an institution title and a semester/exam/year line
when those fields can be identified.
Row-oriented tables continue to produce the compact date/course/venue PDF.
Cohort matching normalizes case, dotted abbreviations, punctuation, whitespace,
and trailing parenthesized student counts. It never uses substring matching;
unmatched and ambiguous cohorts produce errors rather than a PDF.

The representative Test 1 2025/2026 workbook was inspected: it has one sheet,
200 cohort sections, 15 time columns, and nine dated rows per cohort from 5
through 13 January 2026. Each section includes the following week, and its
course and room assignments vary by cohort. Those exam dates are intentionally
read from the exam source; they are not copied from or reconciled with the
tenant's recurring class schedule.

Legacy `.xls` and unrecognized `.xlsx` layouts are rejected with a validation
error. The Test 1 grid workbook supplied during development is covered by a
grid parser and PDF preview test; validate each distinct Test 2 and UE workbook
before publishing it.

## Deployment

`reportlab==5.1.0` is required to generate PDFs. Deploy the dependency with the
application, then apply the shared schema migration:

```bash
python manage.py migrate_schemas --shared
```

No tenant schema migration is required for the new exam source table. Existing
Render startup already runs `migrate_schemas --shared`; verify deployment logs
show the `exams.0001_initial` migration applied before enabling the founder
upload UI. No new environment variables are used. The source workbook remains
private in Django's configured storage; the download endpoint returns only the
selected tenant cohort's generated PDF.

The `NEW` badge uses per-browser `localStorage`. It is cleared only after that
browser receives a successful PDF response. It is not an account-level or
cross-device notification.
