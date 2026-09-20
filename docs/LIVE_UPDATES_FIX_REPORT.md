# Live updates update/delete fix report

Date: 2026-09-21  
Branch: `fix/live-updates-update-delete`

## Baseline

The clean branch baseline was `manage.py test`: **87 tests, PASS**. The test
database was local `test_uni_cot_dev`; no production database or secrets were
used.

## Fail-first matrix

The focused matrix was run before application fixes. It found 5 tests, with 1
error and 4 failures:

| Cell | Test | Before-fix result | Failure |
| --- | --- | --- | --- |
| Cached GET contract | `LiveMutationMatrixTests.test_cached_api_responses_revalidate_and_vary_by_cookie` | ERROR | `_cached_json_response` has no `Cache-Control` header, so it cannot require revalidation or vary by session. |
| Countdown update | `LiveMutationMatrixTests.test_countdown_update_publishes_after_commit_and_changes_etag` | FAIL | The ETag changes, but no `transaction.on_commit` callback is scheduled; publication is immediate. |
| User delete/cascade | `LiveMutationMatrixTests.test_user_delete_invalidates_public_families_and_publishes` | FAIL | Only existing cache invalidation occurs; no notifications for users, public-members, groups, or group-members are published. |
| Property delete | `LiveMutationMatrixTests.test_property_delete_publishes_and_removes_row` | FAIL | The row is deleted and cache invalidated, but no properties notification is published. |
| External record update | `LiveMutationMatrixTests.test_external_record_update_publishes_table_and_record_families` | FAIL | The record cache is invalidated, but table/record notifications are not published. |

The ETag/state assertions for the exercised rows were otherwise valid; the
failures are notification timing, affected-family coverage, and cache-header
contracts. The remaining mutation cells (slider, group CRUD, membership CRUD,
bulk clear/reformat, and external-table CRUD) are included in the post-fix
matrix below and were not marked PASS before their implementation was changed.

## Correction applied

`core.views` now invalidates cache versions and publishes the mapped resource
after the transaction commits. Cached JSON sends `Cache-Control: no-cache`
and `Vary: Cookie` (never `no-store`). The four existing TenantLive adapters
now reconcile successful complete lists; empty success clears the UI and
failed requests preserve the previous state. Owner writes trigger an immediate
resource refresh through TenantLive.

## After-fix matrix and verification

| Check | Result | Evidence |
| --- | --- | --- |
| Cached GET headers/ETag | PASS | `test_cached_api_responses_revalidate_and_vary_by_cookie` |
| Countdown update/delete state + after-commit publication | PASS | `LiveMutationMatrixTests` focused tests |
| User delete cascade + member/group publication | PASS | `test_user_delete_invalidates_public_families_and_publishes` |
| Property delete state + publication | PASS | `test_property_delete_publishes_and_removes_row` |
| External record update state + publication | PASS | `test_external_record_update_publishes_table_and_record_families` |
| Full normal suite | PASS | 107 tests, OK |
| Reverse suite | PASS | 107 tests, OK |
| Shuffle 20260930 / 20260931 | PASS | 107 tests, OK for each seed |
| Compileall / checks / migrations | PASS | compileall, check, deploy check (placeholder warning), makemigrations check |
| TenantLive Node tests | PASS | 1 test file, 1 pass |
| Browser two-device proof | NOT RUN | No browser harness available |

## Verification

No push or deployment was performed. The browser two-device proof remains
manual because no browser harness is available.
