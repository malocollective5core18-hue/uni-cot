# Founder-managed examination timetables

The founder control panel stores Test 1, Test 2, and UE workbooks independently
through the configured Django storage backend. Each source has a retained,
monotonic version, including after deletion. The `exams` app is in
`SHARED_APPS` only; its source table is created in `public` and is not migrated
into tenant schemas. Tenant download requests resolve the cohort from the
current tenant's `TenantTimetableState.state['cohort']`.

## Workbook support

Uploads must be valid `.xlsx` files no larger than 25 MB. Legacy `.xls` is not
supported because the project does not include an `.xls` parser. The initial
parser accepts row-oriented tables with a cohort column (such as `Programme`,
`Class`, or `Cohort`) or a worksheet named for the cohort. It requires a date,
course code or course name, and either a time range or start and end time
columns. Day and venue are optional. Header rows can repeat and merged cells
are expanded before parsing. Cohort matching normalizes case, dotted
abbreviations, punctuation, whitespace, and trailing parenthesized student
counts. It never uses substring matching; unmatched and ambiguous cohorts
produce errors rather than a PDF.

There was no examination workbook in the repository to verify these rules
against the founder's actual layout. Test a redacted representative workbook
from each distinct layout before relying on downloads. Unsupported layouts are
rejected and need a focused parser extension.

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
