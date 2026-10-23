# Operational KPI implementation

`/kpi/` contains exactly original goals 1, 2, 3, 4, 7 and 8. Each card is a normal
link to `/kpi/operations/<revenue|content|reviews|response|issues|cleaning>/`.
The prior dashboard, definitions and APIs remain at `/kpi/north-star/` and their
existing API routes. Goals 5/6 are tracked separately; procurement is excluded.
Owner: Usman. Deadline: December 31, 2026.

## Reads, permissions and cost

`GET /kpi/api/operations` inherits the KPI blueprint feature gate and approved-user
or existing API-key authentication. It accepts no write, verification override,
secret, or historical-as-of parameter. Responses are private/no-store. An internal
single-flight cache lasts 60 seconds per worker and retains its original as-of,
calculation and source timestamps. Visible tabs reread once a minute; refresh
buttons never queue a sync. Background tabs do not poll.

`operations_data.py` projects allowlisted columns from existing internal tables.
Each PostgreSQL transaction sets READ ONLY and an eight-second statement timeout,
rolls back, and closes. No application factory, DDL, classification, external API,
review queue/unresponded route, workflow service or credential change is used.
Messages are bounded at 50,000 rows and explicitly do not claim complete carry-in
coverage. Reservation queries include overlapping stays and prior-year padding.
All active issue rows are considered, including records outside the window and
records missing from the current catalog. No guest names, contacts, bodies or
review text enter the payload. Source exceptions become explicit error states.

## Evidence contract and honest incompleteness

The production adapter deliberately does **not** set the verification flags used
by the pure aggregation engine. Current database columns do not establish the
required contracts. A successful synchronization check supplies freshness, not
semantic verification or completeness. Source checks older than 24 hours are
stale; missing timestamps stay unknown. A passing badge requires complete, fresh
sources, verified metric evidence, all relevant property timezones and the goal.

Every property uses its IANA timezone to determine the current local date and
subtract three calendar months, clamping month ends. Dates are half-open. At the
reference instant on October 23, the window is July 23–October 23, 92 days for
properties whose local date is October 23. Missing timezones produce unknown
window observations. UTC is not substituted. The issue load and overdue queue
are as-of snapshots; issue history uses daily local closing times.

The engine's normalized internal contracts (not public request parameters) are:

| Goal | Required verified evidence | Available observations remain distinct |
|---|---|---|
| 1 | Stable comparable physical-unit mapping; reconciled USD discounted lodging/refunds; complete per-night physical-unit allocations and canonical booking keys | Overlapping booking counts; no substitution of `total_price`, no invented earned revenue |
| 2 | Unique meaningful publication IDs, publication timestamps, complete edit ledger, channel/quality verification | Listing snapshots; pricing is excluded as content-edit proof; clipped edge weeks are separate |
| 3 | Channel-specific verified eligibility/maturation policies; reservation-ID review join; publication-time, native-scale and portfolio verification | Current published review records and raw scores by channel; native 10-point scores are never blindly converted or pooled |
| 4 | Complete original timestamp history, inbound reply-need labels, sent state and meaningful answer labels (human or AI), carry-ins | One timing proxy per inbound sequence; a no-match sequence is only possibly unanswered |
| 7 | Active physical-unit register, complete guest-issue coverage, initial historical issue states plus timestamped guest-issue events | Operationally active records (not distinct incidents); conflicts remain active; ticket transitions cannot reconstruct backlog |
| 8 | Verified qualifying check-ins, approved cleaning-category mapping, reviewed distinct incident IDs and duplicate reconciliation | Cleaning-category candidate records, never the validated numerator |

The pure engine tests the future verified contracts as well as missing evidence.
Enabling certified metrics requires implementing verified producers for these
contracts; changing a Boolean or a source status without the evidence is unsafe.
No schema migration or new secret configuration is included in this change.

## Reference evidence inspected

The saved HTML design version 2 was materialized locally through the current
Library helper. The PriceLabs reservation evidence and sanitized Cotton evidence
were read as snapshots, and the Cotton ZIP checksum matched
`f3aec6af28a8983c8b69cc691b056c3797b8fb7fdba62e453cf51d9c90d6e46f`.
The joined six-goal contract generated October 23 was read from the provided local
path without modifying it. None of these files are runtime actuals or committed
data. Denied native listing IDs 311151, 312300, 323907, 389253, 486217 were never
requested, retried or bypassed by this implementation.

Research caveats remain: catalog entries are not verified physical units; Cotton
and Hostaway review counts must be reconciled rather than added; 61 possible
duplicate groups and 133 cleaning candidates are unapproved; 57 narrative/status
conflicts cannot be auto-resolved. Historical backlog cannot be reconstructed
from current status. The runtime derives observed counts from its configured
database and labels stale/different snapshots; it does not hardcode these counts.

## Verification commands

Use the existing Python environment with test-only environment variables and a
non-production DATABASE_URL. Tests mock routes or use isolated in-memory SQLite
fixtures; they never run the application factory or real migrations. SQLite JSON
storage in fixtures is not a claim of PostgreSQL integration certification.

```sh
python -m pytest dashboard/auth/tests dashboard/kpi/tests dashboard/stay_issues/test_workflow.py dashboard/reviews/tests/test_published_reviews.py -q
OPERATIONAL_UI_QA=1 python -m pytest dashboard/kpi/tests/test_operations_ui.py -q
node --check dashboard/static/js/operations-kpi.js
PYTHONPYCACHEPREFIX=/tmp/operational-pycache python -m compileall -q dashboard/kpi
git diff --check
```

Browser tests use local synthetic fixtures only. They cover desktop/mobile,
exactly six cards, all detail URLs, reload/back/forward, repeated clicks,
empty/loading/error/retry states, no modal, no green with incomplete evidence,
no browser errors and GET-only requests. Screenshots are QA fixtures, not live
performance. No repository lint/type-check configuration was present.

## Main-based verification and release preparation

The original checkpoint is retained on `codex/operational-kpi` at `3d55b30`.
Only that feature commit was transplanted onto main commit
`abd0d09ddd69f6d95abeeb6d4c39198f0e585f87` in a separate worktree. Main's
additional API-documentation navigation is stubbed by the local test application;
production navigation and auth code are unchanged. A regression test confirms
that a guest-issue-only API key cannot read the operational KPI API.

Release preparation remains local-only. Before any authorized release, validate
the intended environment's source timestamps and property IANA timezones through
read-only internal queries/API access, then review the code/static assets and
existing application startup behavior. This feature requires no new schema or
secret configuration, but normal application startup has existing schema setup
outside this feature and must be accounted for in the release procedure. Use an
approved revision, verify the protected overview/detail/API routes after release,
and retain the prior application revision for rollback.

The 60-second cache is per application worker. Browser polling is also once per
minute, so a displayed calculation can be nearly two minutes old plus query
latency. Source data can be much older: recalculation, polling and restarting the
application do not synchronize it. Missing timezone/evidence contracts must remain
unknown even after release. No snapshot or synthetic QA fixture is wired into
production metrics. Deployment does not certify the six goals; verification of
the required evidence producers is separate outstanding work.
