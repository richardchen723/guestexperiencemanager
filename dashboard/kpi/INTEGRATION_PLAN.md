# Operational KPI source-integration checkpoints

This is the remaining local implementation plan, not a production-ready claim.
The current release prioritizes persisted 48-hour calculations and documented
estimates. Listing edit-history/refresh tracking and historical extraction are
deferred for team discussion. The remaining producer work below is retained for
a later integration phase. This document does not authorize additional production
writes. Existing feature/API-key gates and legacy KPI routes stay intact.

1. **Timezone ingestion (first checkpoint).** Parse Hostaway `timeZoneName` and
   legacy `timezoneName`, validate IANA identifiers, retain valid stored zones
   when source fields are absent/invalid, and surface conflicting aliases.
   Store source/validation evidence under numeric listing IDs in the existing
   `sync_logs.listing_stats` JSON. Test ingestion through calendar-window logic.
   No new column or migration is required. This corrects future approved syncs;
   it does not automatically fill existing database rows.
2. **Read-source contracts.** Replace unconditional `partial` with per-source
   evidence: read success, scoped expected/observed IDs, per-property field
   coverage, source interval, pagination/truncation/errors, last source check,
   raw provenance and calculation time. A successful global sync is not proof
   of complete property/window coverage. Domain certification remains separate
   from raw-source availability. Missing coverage stays unknown, not zero.
3. **Existing source joins.** Connect stored listing-audit runs/snapshots and
   channel-check evidence for current listing/quality diagnostics; retain source
   timestamps and partial audit coverage. Audits do not prove historical edits.
   Join reviews to exact reservation/listing/channel IDs, retain archived and
   left-boundary checkout opportunities, and show provisional coverage with
   explicit cancellation/publication/scale gaps. Read live guest-issue workflow
   records without rewriting them from narrative classifications.
4. **New upstream ledgers where necessary.** Define and test additive producers
   for nightly lodging/refunds and bundle allocations; effective-dated physical
   unit/cohort mappings; meaningful content publications; original inbound and
   dispatched answer/semantic evidence; issue status events plus opening state;
   and reviewed cleaning incident identities/check-in eligibility. Stage any
   migrations/backfills for review, never infer missing historical events from
   current status. Populate verification from these producers, not Boolean
   overrides. Do not fabricate historical edit/backlog series.
5. **End-to-end readiness.** Test complete, partial, missing, inaccessible, stale
   and truncated sources, calendar/coverage boundaries and joins. Verify accurate
   source freshness versus calculation time and all six routes/states. Document
   each still-blocked actual. Production job health/cadence must be checked before
   a separately approved release; deployment alone does not certify metrics.

## Safe timezone backfill design (not executed)

Use a metadata-only, explicit dry-run/apply workflow after approval rather than
running the broad existing listing sync solely for timezone repair. The latter
also updates other metadata/photos/tags and listing-removal state.

The dry run should accept an authorized native-catalog snapshot with its source
timestamp/hash, read only listing IDs/current timezone values, apply the same
resolver, and emit an allowlisted diff with old/new zone, source field and issues.
Known inaccessible IDs must remain excluded; never request or retry them.
Conflicts, invalid fields, missing IDs and stale evidence require review.

An eventual apply command should require the exact reviewed plan/hash, restrict
updates to timezone_name only, compare-and-set the reviewed old value in one
transaction, record provenance separately from a source-wide sync timestamp,
and support idempotent retry. It must not advance unrelated `last_synced_at` or
claim fresh reservation/message/review coverage. No apply code has been run or
approval implied; existing valid rows must not be overwritten by missing data.
