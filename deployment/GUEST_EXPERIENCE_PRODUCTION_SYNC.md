# Guest-experience result replication

The Codex scheduled task analyzes private stay and review inputs on the local
Mac. After each local import, it streams a result-only JSON payload over SSH to
the application server and imports it into the server's local PostgreSQL
`brain` schema. PostgreSQL port 5432 remains private.

Deploy the application code before enabling the production step so the remote
server has the `sync-import` command and guest-experience tables:

```bash
./deployment/update-ec2.sh
```

The local production sync command is:

```bash
.venv/bin/python -m brain.guest_experience_codex sync-production \
  --run-id RUN_ID \
  --ssh-target ubuntu@PRODUCTION_IP \
  --identity-file /absolute/path/to/key.pem
```

Use `--pending` instead of `--run-id` to retry all unsynced result batches,
including batches older than the scan window. A successful production import is
recorded in the local analysis run. Failed or interrupted transfers remain
pending and are safe to retry.

The SSH target and key can alternatively be configured with:

- `GUEST_EXPERIENCE_PRODUCTION_SSH_TARGET`
- `GUEST_EXPERIENCE_PRODUCTION_SSH_KEY`
- `GUEST_EXPERIENCE_PRODUCTION_SSH_PORT`
- `GUEST_EXPERIENCE_PRODUCTION_APP_DIR`
- `GUEST_EXPERIENCE_PRODUCTION_PYTHON`
- `GUEST_EXPERIENCE_PRODUCTION_ENV_FILE`
- `GUEST_EXPERIENCE_PRODUCTION_TIMEOUT_SECONDS`

Delivery snapshots are immutable per run. Stays carry a monotonic scan version;
production accepts newer versions, ignores older versions, and rejects conflicting
content at the same version. Issue updates carry their analysis timestamp and
retain stable source keys. Database uniqueness prevents inserting the same stay's
complaint twice. A message and review can attach to the same issue row.

Only stored analysis rows and evidence identifiers are replicated. Raw guest
messages, stay notes, and raw public/private review text are not part of the
replication payload. Existing production issue resolutions, operator comments,
and ticket links are preserved on retries.

## Meaning-based complaint consolidation

Guest issue categories are display labels, not grouping boundaries. During the
existing Codex analysis, the exporter now supplies `complaint_catalog` and
`instructions.complaint_matching`. Read those instructions along with the full
stay/review evidence. Include `complaint_key` on every new issue: reuse the
catalog's key when the complaint concerns the same item, location, and defect,
regardless of wording or category. For unclassified catalog reports, return the
requested top-level `complaint_assignments` as well as `stays` and `reviews`.
The importer validates the exported evidence hashes and preserves existing keys.
No additional model API calls occur in the app or during page loads.

Both dashboard rendering and issue actions use the saved identities. Different
properties, separate tickets, and resolved versus active incidents remain
separate. Reports of different problems with similar wording also remain
separate when Codex assigns distinct identities. Reports without an identity
continue using conservative text matching, without a category restriction.
Counts still represent distinct stays; messages and a review from one stay
count once.

Deploy the additive `complaint_key` migration and importer on production before
running the updated local exporter. Result replication now emits schema version
3 so an older server refuses the payload instead of silently dropping updates.
The updated server still accepts completed version 1 and 2 delivery retries.
Finish existing in-flight analysis batches before deploying selection or input
schema changes; newly exported batches require the current identity fields.

To classify existing reports without reanalyzing raw messages or changing issue
statuses, export one property (omit `--listing-id` to include all properties with
unclassified reports):

```bash
.venv/bin/python -m brain.guest_experience_codex export-complaints \
  --listing-id LISTING_ID --output /tmp/complaint-batch.json
```

Have Codex read the entire packet and follow its instructions, writing a results
file with `complaint_assignments`. Every previously unclassified report must
receive a specific identity. Existing non-null identities must remain unchanged.
Then import the decisions:

```bash
.venv/bin/python -m brain.guest_experience_codex import-complaints \
  --batch /tmp/complaint-batch.json --results /tmp/complaint-results.json
```

The command returns `run_ids_to_sync`. Replicate **each** of those runs using
`sync-production --run-id RUN_ID` with the SSH options above, including runs
older than the current analysis window.
Only identity metadata is added to existing production issues; resolution notes,
statuses, ticket links, and guest evidence remain intact. Remove the temporary
packets after verification. New analysis batches also classify unassigned reports
in their properties' catalogs, so those existing identities travel with the next
result replication.
