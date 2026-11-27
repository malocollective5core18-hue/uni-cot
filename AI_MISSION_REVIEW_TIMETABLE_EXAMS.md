# AI Mission: Pre-Production End-to-End Review of UNI-COT Timetables and Exam PDFs

Act as a Principal Django Engineer, multi-tenant SaaS security reviewer, QA lead, and office-document/PDF usability reviewer. Conduct a **read-only, pre-production end-to-end review** of the current UNI-COT working tree. Do not edit files, commit, push, deploy, upload workbooks, or alter databases or credentials.

## Review source: GitHub production branch

- Review the GitHub repository `malocollective5core18-hue/uni-cot`, branch `admin-controller-fix`.
- This is the production-target branch containing the latest timetable/PDF implementation. Confirm its current HEAD and verify that commit `68a75fe` (`[P0-1] restore full exam PDF grid for A4`) is an ancestor of that HEAD before reviewing.
- Review the files from that exact GitHub branch snapshot. Do not review `main`, another feature branch, a stale local checkout, or an uncommitted working tree instead.
- If using a local clone, fetch and check out `origin/admin-controller-fix` in a separate temporary/read-only worktree or clone. Do not change the user's existing checkout. Record the full commit SHA and branch in the report, and link findings to that commit's files/lines where possible.
- If GitHub access is unavailable, or the branch does not contain the expected commit, stop and report **Not verified** with the access/ref evidence. Do not silently substitute another branch or claim the GitHub review was completed.

## Review objective

Determine whether the timetable and exam timetable flow works from founder upload through parsing, tenant/admin management, publication, member polling, and final PDF download. Identify concrete defects and release risks with evidence. Distinguish verified behavior from assumptions. Do not infer that a feature works merely because a template contains its button or a unit test passes.

## User requirements to assess

1. **Admin and member timetable:** Tenant admins manage timetable entries; normal tenant readers see the same tenant timetable read-only after publication. Changes such as KIPO/HAKIPO, venue changes, swaps, and temporary entries should be persisted and visible to readers under the existing publication and revision rules. Preserve schema-per-tenant isolation and stale founder-source protection.
2. **Public `/system/` demo:** The global public demo remains browser-local and must never write to or read arbitrary tenant timetable state. Its Test 01 demonstration is a fixed BAE (65) sample from the supplied Test 1 workbook, not BAE (54), and the demo exam route must not query tenant or database data. Test 02 and UE demo buttons may be unavailable if no fixture is present.
3. **Reader presentation:** Read-only timetable content must not be independently hard-coded or silently diverge from the admin data. Course code, full course name, lecturer, and venue should render without unexplained truncation where the admin shows the full values. Reader layout should match the admin table’s page-level flow and must not introduce an inner scrolling table inconsistent with that layout.
4. **Exam workbook parsing:** Support the supplied wide grid-format workbook, select the exact saved cohort, retain dates across multiple weeks, map each exam cell to the proper time and venue, and keep other cohorts isolated.
5. **Exam PDF:** Preserve every source time column, including empty periods, in the full day/time grid and colors, matching the regular class timetable's compact landscape A4 print layout. Retain every exam, date, course, venue, and explicit unavailable marker. Long multi-week schedules may paginate by dates while keeping the complete source time grid. The Test 01 title should read `NATIONAL INSTITUTE OF TRANSPORT` and a clear second line `FIRST SEMESTER    TEST I    YEAR 2025/2026`. The class timetable print stylesheet must not print the examination download panel. Determine whether the actual output is legible on A4 paper and a phone; use a current PDF preview or regenerate a temporary preview from code if safe, and inspect it visually. Consider ordinary grayscale office printing, long venue/course strings, empty exam dates, and multi-week page breaks.
6. **Download controls:** Explain exactly when the public demo button and tenant Test 01/Test 02/UE buttons are shown, and whether the local public popup route is configured as intended.

## End-to-end review questions

Answer each explicitly with **Yes / No / Not verified**, evidence, and any blocker:

1. Will the supplied BAE (65) Test 01 PDF be legible at actual landscape A4 print size, with no clipped or missing text and appropriate page count?
2. Does it retain all 15 source time columns and all five BAE (65) exam entries with their correct dates, times, and venues?
3. Does a founder upload travel through validation and private shared storage to only the intended tenant's cohort-specific PDF?
4. After an admin changes KIPO/HAKIPO, venue, swap, or temporary-class data and publishes, does the same tenant's reader see the update through polling, while unpublished/stale data and other tenants remain isolated?
5. Is the global demo's sample download visible and browser-local, with no tenant/database reads or writes?
6. Do all negative cases fail safely: malformed/oversized workbook, missing or ambiguous cohort, unsupported exam type, absent file, stale source, permission denial, and PDF-generation error?
7. Is the implementation ready for production now? If not, list the smallest blocking fixes and the exact evidence needed to clear them.

## Required review method

- Start with `git status --short`, branch name, and complete changed-file inventory. Treat all pre-existing and untracked work as user changes; do not clean or revert it.
- Read `AGENTS.md`, relevant docs, and each changed file before drawing conclusions.
- Trace the full request/data flow from template to JavaScript to Django URL/view/service/model and back to the rendered timetable/PDF. Search every caller before judging whether an API or behavior is unused.
- Check permission gates, tenant schema contexts, publication/source-version checks, optimistic revision checks, cache keys, response headers, and whether any public route can expose tenant uploads.
- Trace the whole exam flow: founder-only upload/delete and validation; public-schema source/version/storage; tenant state/cohort selection; PDF endpoint; source-version cache key; response filename/content type; browser metadata/polling/button visibility/download. Trace timetable edits through saves, publication, reader API and polling too.
- Inspect tests and their actual recent command results. Run focused checks only when safe. Do not create or modify a database. If the full DB-backed suite is blocked, state the exact environment failure and do not imply end-to-end verification passed.
- Inspect generated preview PDFs and establish whether each preview matches current code. If useful and safe, generate a new preview only in a temporary directory; do not overwrite user previews. Inspect page size, page count, extracted text, and a rendered page image.
- Evaluate a practical office/phone review path. If browser automation or a printer is unavailable, mark visual/device claims unverified and give the exact manual acceptance steps.
- Review the accumulated diff for accidental duplication, contradictory documentation, dead code, and unrelated changes.

## Report format

### Verdict
Choose one: **READY**, **READY WITH RISKS**, or **NOT READY**. Explain the verdict in a few sentences.

### Findings
Order findings by severity. For every finding include:

- Severity: Critical / High / Medium / Low
- File and line(s)
- Concrete failure mode and affected user
- Evidence and a practical reproduction/verification step
- Smallest safe recommended fix

If there are no findings, say so explicitly and state what you verified.

### Requirement matrix
For each of the six requirements above, mark **Pass / Partial / Fail / Not verified**, with short evidence and any missing verification.

### End-to-end questions
Answer the seven questions above directly. Do not use a general statement such as “looks good” in place of evidence.

### Verification
List exact commands run and outcomes. Separate passed checks, failed checks, and blocked checks. Explicitly disclose PostgreSQL/test database limitations, existing deploy warnings, or unavailable browser/PDF visual checks.

### Data safety and deployment
State whether tenant isolation/publication protection appears intact, whether any real/production data was accessed, whether the PDF is demonstrably office-ready, and whether the implementation is ready to deploy. Do not suggest deployment if a High/Critical issue remains or the end-to-end publication/download path is unverified.

Be specific, skeptical, and concise. Avoid proposing a redesign or optional UI changes outside the listed requirements.
