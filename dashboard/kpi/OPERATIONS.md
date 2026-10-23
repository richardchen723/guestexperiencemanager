# Operational KPI scorecard

Exactly six original goals appear at `/kpi/`: 1, 2, 3, 4, 7, 8. Normal links open
concise separate details. Goal 2 listing refresh/edit history is **deferred for
team definition**, not failed or zero. Goals 5/6 are tracked separately;
procurement is excluded. Owner Usman; deadline December 31, 2026. Legacy
north-star routes remain.

## Persisted 48-hour results

`GET /kpi/api/operations` calls `read_saved_snapshot()` via the HTTP adapter and
reads one joined snapshot/state record. It never calls source reads, aggregation,
DDL, source APIs or jobs. Overview/detail HTML renders the shell. Reload snapshot,
normal reloads, navigation and visible-tab polling read saved results only. The
legacy `/kpi/api/data` retains its prior behavior; this page never calls it.

`python -m dashboard.kpi.snapshot_job run-due` is the separate background command.
Durable `next_due_at` enforces 48-hour calculation slots. The systemd timer checks
that inexpensive state hourly; it does not aggregate hourly. No scheduler loop
runs in web workers. A two-hour lease, generation/token fencing and unique
contract/slot constraint prevent overlapping publishers; expired attempts recover
the same logical slot. Missed historical slots are not replayed as backfills.

All six metrics and the shared pointer publish in one transaction. Failed source
reads, incomplete/invalid payloads and insertion failures retain the previous
snapshot plus safe failure status. Cold/missing storage returns static
placeholders; unsupported contract versions await new publication. GET never
initializes storage or calculates, even when no saved result exists.

Metadata separates calculated_at, window_as_of, source_as_of (per-source actual
checks/provenance), and next_due_at. Normal snapshot age does not turn stale
at 24 hours. Upstream stale/incomplete warnings are assessed at the calculation
cutoff, independently of overdue/failed scheduling. Unknown checks stay unknown.

## Storage and release

The two new models use independent metadata, excluded from app-startup DDL.
`deployment/sql/operational_kpi_snapshots.sql` contains additive DDL; alternatively
`python -m dashboard.kpi.snapshot_job init-storage` explicitly creates only these
tables. Existing brain schema required. No existing source records, credentials
or roles change. After reviewed release prechecks, initialize storage, install
the service/timer, run the first controlled calculation and enable the timer.
Page loads and the general deployment script never silently enable this job.
Review preexisting application-startup DDL/DML before restarting production.
Retain the prior revision and stop the new timer on rollback; saved tables can
remain without deleting data.

## Explicit estimates and provenance

The job reads configured internal Hostaway/Cotton tables only. Library files,
local historical extracts and QA fixtures are never runtime actuals. Source reads
use READ ONLY transactions and timeouts. At most 50,000 message rows are read;
text is used only for a background relevance heuristic and never returned.
Guest names/contacts, message/review bodies and channel booking references do
not enter saved scorecards.

| Goal | Estimate/observation | Limits |
|---|---|---|
|1|Known-fee-adjusted USD booking value prorated over nights; each common listing compared separately|Missing tax/cleaning excludes bookings. Refunds, discounts, deposit inclusion and pass-throughs remain unreconciled. Common listing IDs proxy for physical properties; shared channel-booking IDs across listings are excluded for bundle ambiguity.|
|2|Deferred for team definition|No edit ledger/refresh tracking implemented.|
|3|Provisional rolling-checkout coverage with exact reservation/listing/channel joins; separate review-date rating cohort|Includes recent checkouts rather than waiting for full maturation. Reviewed, unreviewed and still-open opportunities are separate. Open-window flags use a verified policy when provided, otherwise assumed Airbnb 14 / Vrbo 365 / Booking 90 days. Cancellation scenarios are conditional, not confidence intervals. Ratings remain native and separate by channel/scale; verified source-specific scale provenance is required for a /5 comparison. No automatic conversion. Tag-based portfolio mapping remains unverified. Targets remain EH/LR/MF/CR >4.92, SG >4.9, US >4.8.|
|4|Recorded inbound-to-relevant-outbound timestamp proxy, distinct exchanges|Generic acknowledgements/unrelated schedules excluded heuristically; triggered instructions may qualify. Original send/dispatch/answer quality unverified. Missing matches do not prove missed requests.|
|7|Operationally active load across all source dates including carry-ins; only exact saved complaint identities consolidated|Narrative-resolved conflicts stay active. Comparison with catalog entries (unit proxy) is provisional, never actual physical-unit compliance. No fabricated daily history.|
|8|Affected booked check-ins per 10 with cleaning tags, active AND resolved records|This approximation is not distinct incidents. Separate strict saved-identity, strict source-record and ambiguous-category scenarios are shown; unmatched joins excluded/count shown. Original distinct-incident goal retained without a compliance claim. Actual arrival/package identity and classification coverage unverified.|

Timezones use synchronized IANA values or explicitly supported US jurisdictions
with provenance; no silent UTC or split-state guesses. Local windows subtract
three calendar months with month-end clamping. Archived review cohorts and exact
left-boundary checkouts remain included even with zero revenue-window nights.
Further integration gaps remain in INTEGRATION_PLAN.md; historical extraction and
edit tracking are deferred.

## Validation

Isolated DB/fixture tests cover repeated GET zero source/calculation calls, first
publication, 48-hour cadence, cold/missing storage, atomic failure/last-good,
concurrency/lease fencing, contract/window versions, source freshness versus age,
permissions, independent estimate arithmetic, rounding boundaries, nullable rows,
unknown/error data, closed cleaning incidents, carry-ins/timezones, and browser
navigation/loading/error/empty/mobile states. Fixtures do not certify production
data accuracy. Run regression and opt-in browser tests before release.

```sh
python -m pytest sync/tests dashboard/auth/tests dashboard/kpi/tests dashboard/stay_issues dashboard/reviews/tests -q
OPERATIONAL_UI_QA=1 python -m pytest dashboard/kpi/tests/test_operations_ui.py -q
node --check dashboard/static/js/operations-kpi.js
```
