# Daily guest issue scan

The existing Codex scheduled task runs daily at noon America/New_York, with
daylight saving handled by the schedule's timezone. Analysis uses the Codex
subscription. Python only selects inputs, validates results, and replicates them.

## Selection and scan history

1. Retry pending production deliveries, regardless of age.
2. Run `python -m brain.guest_experience_codex refresh-inputs`. This refreshes
   reservations and submitted/published reviews. Review pagination includes old
   reservation IDs so an old stay's newly submitted review remains discoverable.
3. Select confirmed stays departing on today's Eastern calendar date, including
   stays whose scheduled checkout is later today, plus checkouts within the prior
   72 elapsed hours. Property timezone and checkout time determine elapsed age.
4. Refresh complete Hostaway conversations before comparing message IDs. Analyze
   unscanned stays or stays with at least one message ID absent from the last
   successful scan. New messages from either guest or support trigger a rescan;
   delayed synchronization is detected even with an older source timestamp.
5. Select guest reviews with a source posting/submission timestamp within the
   last 36 hours, independent of checkout. Successfully analyzed review IDs are
   skipped permanently. `updatedOn`, `insertedOn`, and date-only values do not
   establish posting recency. Missing timestamps are counted in run diagnostics.

The latest stay row records scanned message IDs, last successful scan time and a
monotonic `source_metadata.scan_version`; legacy rows default to version 1 and
already retain the message IDs needed to skip unchanged stays. Muted stays also
receive this marker. Failed validation rolls back all analysis and marker writes.
Unfinished batches retain their reservations/reviews for retry after expiration,
even if they have left the normal window. A successful retry clears that exception.

## Issue identity and rescan updates

Every exported stay/review includes its existing issues and stable source keys.
The AI must return those keys for recurring complaints and preserve the complaint
identity across wording changes. A new complaint on a rescan requires a new guest
complaint message. An existing issue's evidence is accumulated and its analysis
assessment is updated, without replacing operator workflow, priorities, notes,
resolutions, or tickets. Omitted older issues are retained.

New issues have a database-unique identity built from property, reservation (or
review when no reservation is available), and complaint key. Message and review
evidence from the same stay share one issue. Different stays remain separate
reports; the existing dashboard groups matching open complaints by property and
counts each stay once. Closed operational incidents remain closed.

## Delivery and dashboard

Each completed run freezes a result-only snapshot in its run details before the
latest analysis can be replaced. Replication schema 3 supports versioned stay and
issue updates; old delivery retries cannot overwrite a newer scan. Pending
deliveries never expire because of the eligibility window. PostgreSQL advisory
locks and batch reservations prevent overlapping work.

Dashboard date filters retain a month of history and now include today's scans.
The Scanned stays section displays each reservation's latest scan in Eastern Time.
The 72-hour selection limit does not shorten dashboard history.

Deploy database additions and the schema-3 importer before enabling the updated
schedule. Run the guest scan, replication, identity, dashboard, and review sync
tests before deployment. Keep the local computer and Codex app running at the
scheduled time; a missed run does not expand the normal 72/36-hour windows.
