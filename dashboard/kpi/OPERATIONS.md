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
|1|USD booking value less known components, prorated over nights; each common listing compared separately|Blank tax is included as an unknown-tax estimate; tax is subtracted only when recorded. Invalid nonblank tax, missing/invalid total or cleaning, non-USD and negative adjusted values remain excluded. Refunds, discounts, deposit inclusion and pass-throughs remain unreconciled. Common listing IDs proxy for physical properties; shared channel-booking IDs across listings are excluded for bundle ambiguity.|
|2|Deferred for team definition|No edit ledger/refresh tracking implemented.|
|3|Provisional rolling-checkout coverage with exact reservation/listing/channel joins; separate review-date rating cohort|Includes recent checkouts rather than waiting for full maturation. Reviewed, unreviewed and still-open opportunities are separate. Open-window flags use a verified policy when provided, otherwise assumed Airbnb 14 / Vrbo 365 / Booking 90 days. Cancellation scenarios are conditional, not confidence intervals. Ratings remain native and separate by channel/scale; verified source-specific scale provenance is required for a /5 comparison. No automatic conversion. Tag-based portfolio mapping remains unverified. Targets remain EH/LR/MF/CR >4.92, SG >4.9, US >4.8.|
|4|Recorded inbound-to-relevant-outbound timestamp proxy, distinct exchanges; ≥30-minute count/percentage with zero target|Same matched-pair sample as the under-five-minute measure. Generic acknowledgements/unrelated schedules excluded heuristically; triggered instructions may qualify. Original send/dispatch/answer quality unverified. Missing matches and missing timestamps remain separate from confirmed late pairs.|
|7|Operationally active load across all source dates including carry-ins; only exact saved complaint identities consolidated|Narrative-resolved conflicts stay active. Comparison with catalog entries (unit proxy) is provisional, never actual physical-unit compliance. No fabricated daily history.|
|8|Stays with recorded cleaning issues versus total eligible booked stays|Each stay counts once, including active and resolved issues. The page shows counts and percentages by week/property. Existing category matching and check-in eligibility remain unchanged. Ambiguous-category, saved-identity and source-record audit scenarios remain in the payload only.|

Timezones use synchronized IANA values or explicitly supported US jurisdictions
with provenance; no silent UTC or split-state guesses. Local windows subtract
three calendar months with month-end clamping. Archived review cohorts and exact
left-boundary checkouts remain included even with zero revenue-window nights.
Further integration gaps remain in INTEGRATION_PLAN.md; historical extraction and
edit tracking are deferred.

## Revenue weekly comparison

The revenue detail comparison uses the same common-listing cohort as its growth
summary. Current Monday buckets are clipped to each local reporting window, and
their exact month/day boundaries are shifted back one calendar year with the
same month-end clamping as the goal. This is a calendar-anniversary comparison,
not an ISO-week or 52-week shift. Both dollar columns use observed prorated
booking proxies; refunds remain unreconciled. Edge weeks are labeled. Unequal
leap-year lengths keep their dollar totals but have no YoY percentage. Missing
observations remain null; an observed zero remains numeric, and a zero prior
denominator has no percentage. The two chart series use these same saved values.
Every contributing property must have observations in the relevant weekly
period before that dollar column is shown. A missing property is not assumed to
have zero bookings; row coverage identifies the missing portion. Cross-property
current/prior observations cannot produce an unsupported YoY percentage.

`revenue-weekly-yoy.v1` is an optional revenue-trend schema inside the compatible
snapshot contract. Old snapshots keep all other metrics visible and show the
weekly comparison as unavailable; GET never upgrades or computes it. A later
scheduled background calculation fills the new fields. Immediate visibility
after a release would require a separately controlled one-time snapshot rebuild;
the ordinary `run-due` command still respects the existing 48-hour slot. The
removed assumptions/property-window UI does not delete stored audit metadata.

The `known-components-unknown-tax.v2` calculation counts unknown-tax inclusions
and real financial/currency exclusions separately, in both relevant windows,
before common-cohort matching. It does not zero-fill any source field. Old saved
results retain their earlier policy and broader exclusion counter, explicitly
labeled as earlier-policy data until a background publication replaces them.

The review weekly table defaults to all three channels, labels Monday checkout
buckets with the requested visible headers, and keeps each row's original
target. Optional channel selection survives automatic reads and page reloads.
Absent weekly channel data is explicit; no 0% rows are synthesized. The sprawling
review breakdown is hidden without removing its underlying snapshot evidence.

`matched-replies-30m.v1` adds a detail diagnostic to goal 4, not a seventh card.
It uses an inclusive unrounded ≥30-minute threshold and the existing matched
sample denominator, counts unmatched candidates separately, and lists at most
25 longest pairs with recorded timestamps/property/thread references. Untimed
messages are excluded from timing and identified as unplaced evidence. Missing
new fields in old snapshots show unavailable, never an inferred zero. A normal
scheduled calculation or separately approved controlled rebuild is needed to
publish the new tax policy, comparison fields and 30-minute diagnostic.

Cleaning uses optional `affected-stay-share.v3` fields with cached exact counts
and percentages. The simple page does not expose technical audit scenarios or
duplicate supporting panels. Old snapshots without those fields show unavailable
weekly percentages, without reversing a rounded per-ten rate into a numerator.

An authorized release can run `snapshot_job refresh-once --revision
operational-kpi.2026-10-24-details.v1` once. It uses the same durable lease,
generation fencing, source reads and atomic publication as `run-due`; an already
published revision is idempotent. A future existing next-due timestamp remains
unchanged. Failures retain the last good snapshot and its history. No HTTP route
can request a refresh, and the timer continues to call only `run-due`.

## Controlled release and rollback

`deployment/operational_kpi_release.py` consumes a reviewed nine-file runtime
manifest (seven preimages, two new paths). It stops/disables the timer and stops
both regular and one-time workers before capturing the existing saved-snapshot
control state. It backs up the seven files, checks every hash again, then applies
only those paths. No migration, source repair or credential change is included.

The explicit refresh action records the resulting state and accepts only
`published` or `already_current`, with the expected saved revision and snapshot
ID. Timer enablement requires that checkpoint. The importer release/targeted
corrections must be coordinated separately before this calculation.

Rollback checks every original file hash and every recorded state field. It
restores the previous saved-snapshot pointer **before** restoring the seven
original files, with the dashboard stopped. The generation increases to fence
any late worker; leases are cleared and the old schedule is restored. Both old
and new snapshot history rows remain. The timer stays disabled. A changed
snapshot checkpoint or file blocks restoration rather than overwriting later
work. File-only rollback is unsafe: the prior UI interprets cleaning's `value`
as per-ten, whereas the new snapshot stores a percentage. Review the recorded
checkpoint after any uncertain release outcome before retrying.

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
