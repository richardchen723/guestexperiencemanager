# Guest issue scan verification — September 23, 2026

The new policy is deployed to production. The existing scheduled task is active
at noon America/New_York daily, starting September 24. Its saved timezone rule
was checked across the November and March daylight saving transitions: it stays
at noon Eastern and moves between 16:00 and 17:00 UTC.

## Automated checks

- 81 focused tests passed on the latest main branch across scan selection, analysis validation, issue
  identity, replication, dashboard grouping/workflow and review synchronization.
- Both PostgreSQL migration regression tests passed on the same checkout.
- All 16 incremental scan tests also passed against the merged production code
  in an isolated test directory on the server.
- Coverage includes same-day checkout before its scheduled time, 72/36-hour
  boundaries, unchanged-message skips, late-imported messages, stable issue IDs,
  message/review consolidation, stale-input rejection, atomic failed imports,
  interrupted-batch retries, immutable deliveries, out-of-order replication and
  preservation of operator workflow and ticket links.
- The production dashboard rendered scan history and Eastern timestamps, and
  the health endpoint confirmed the application and database were healthy.

## Live scan and replay

Inputs were refreshed from Hostaway. Local runs 179–181 were imported without
validation errors and delivered as production runs 122–124.

| Result | Count |
| --- | ---: |
| Eligible stays | 37 |
| Unchanged stays skipped on first selection | 11 |
| First scans | 16 |
| Rescans after new messages | 10 |
| Stays departing September 23 included | 9 |
| Muted stays recorded, included in the above scans | 2 |
| Recent reviews scanned | 4 |
| New issue reports | 17 |
| Existing issue reports updated in place | 4 |
| Remaining eligible backlog | 0 |
| Pending production deliveries | 0 |

Repeat selection (run 182) refreshed conversations again and skipped all 37
stays and all four reviews. It exported no work. Local counts remained 856 stay
analyses, 238 review analyses and 963 issues.

Replaying run 179 into production created no rows and performed no analysis
updates: its eight stays, four reviews and six issue records already existed.
Production retained 855 stay analyses, 238 review analyses and 963 issues. All
26 scans from this session appeared in its dashboard. The difference between
historical local and production stay totals predates this scan.

No duplicate non-null issue dedupe keys were present. A repeated production
snapshot comparison confirmed unchanged scan markers, operator statuses,
resolution comments, priorities and ticket links. An initial comparison detected
a concurrent operator resolution on unrelated issue 565; its timestamped
operator activity explained the difference. A subsequent delivery replay left
the full snapshot unchanged.

## Source and scheduling limits

Reviews without a precise source posting timestamp are excluded and counted,
rather than assigned an invented posting time. The current API refresh reported
three undated guest reviews; 655 retained local guest-review records lack a
precise timestamp. The four reviews with verified timestamps inside the 36-hour
window were all scanned successfully.

The existing scheduled task runs on the local Mac. The computer and Codex app
must be running for scheduled execution. Missed schedules do not expand the
normal eligibility windows; interrupted exported batches and pending deliveries
retain their explicit retry paths.
