# Guest Inquiry CRM — ticket 813

Native Flask/Jinja CRM at `/inquiries/`. This implementation was grounded in
the live ticket, its two attachments, the complete Google Doc and ticket comments
710 and 711. The later addendum controls the first release: website inquiry email
intake, spreadsheet management, guest reply updates, follow-ups and qualification.

Richard confirmed on October 20, 2026:

- Intake mailbox: `larry@enchantedhavens.com`.
- Staff timezone: `America/Los_Angeles`, including daylight-saving changes.
- Reminder/escalation intervals: to be specified; leave them unset.

## Built in this branch

- Manual staff capture through **New lead** (ticket 818). Guest name and at
  least an email or phone number are required; stay details are optional. The
  same side panel supports property/tag, qualification, assignment, status and
  accountable follow-ups. Manual creation records the staff actor and original
  notes without inventing a source email or guest response. Creation UUIDs make
  retries safe even when the successful response is lost. Internal-test entry
  remains administrator-only and excluded from genuine statistics.
- All 11 spreadsheet fields plus Expand, sticky headers, horizontal scrolling,
  server pagination, search, sortable receipt/check-in/agent/status columns,
  inline notes/assignment/status, and a side panel preserving table position.
- Five statuses and filter-aware statistics. Conversion counts Booked divided
  by Booked plus Closed — Not Booked; no completed leads displays a dash.
- Existing approved team accounts, properties, property aliases and tags.
  A separate `inquiries` feature grant defaults off for regular accounts;
  owners/admins inherit it. Grant reservations/support staff through Team access.
- Separate guest profiles, inquiry opportunities, immutable source messages and
  append-only audit events in the additive `inquiries` PostgreSQL schema.
- Immutable source IDs, mailbox/thread/reference matching, retry-safe ingestion,
  separate trips for separate references, and review of ambiguous thread identity.
- Guest-reply extraction updates supported guest fields while preserving internal
  notes, staff corrections, assignment and schedules. Conflicting/older values
  are proposed in review flags. New responses remain visible until acknowledged.
- Owner/action/due time required for Follow-up Required. Unassigned age, overdue,
  configurable reminder/escalation flags, and the shared reservations queue.
- Adults/children/count, pets/requirements, flexible/alternative dates, budget
  exactly as supplied including currency, contact preference and quote status.
  The application makes no payments or quotes; its currency default remains USD.
- Optimistic versions reject stale staff changes. A transaction advisory lock
  serializes intake/idempotency and staff writes across web workers.
- Durable processing failures with source retention and administrator retry,
  browser CSRF, bounded body reads, escaped messages and private/no-store output.
- A conservative structured-email parser and authenticated intake bridge for
  Larry's natural-language extraction through the existing Gmail connector.

## Production release and remaining activation

Deployed October 20, 2026 at `https://yourcottoncandy.com/inquiries/` as
`inquiry-crm-20261020-r1`. Production has the additive schema and default settings,
with no imported leads or source messages at release. The release preserved live
property archives, management logs and authentication changes. Backups are at
`/opt/hostaway-messages/backups/inquiry-crm-20261020-r1`.

No staff grants, inbox rules, scheduled jobs or API keys were created or changed.
No guest-facing messages were sent. Existing websites/forms were not changed.
The observed Emerald Haven inquiry thread explicitly says it is an internal test;
it was inspected for format only and was not imported as a lead.

The CRM server does not poll Gmail or call another model. Larry supplies authorized
Gmail reads and structured extractions to `POST /inquiries/api/intake`. Grant the
intended team access, bind Larry's existing authorized production runtime
to that endpoint, then run a genuine or explicitly marked test submission and
reply to verify the complete live path. A recurring inbox run should use message
ID deduplication and an overlapping lookback, process replies from known inquiry
threads even when their subjects change, and record unresolved failures. Do not
call the source connected until this live check succeeds.

Production currently has an active full-access key named Larry Key. Its raw value
was not retrieved or copied during deployment. Larry must use the credential
already available in his authorized runtime, or report that credential access is
missing. A separate restricted Guest Issues adapter must not be repurposed.
See `deployment/INQUIRY_CRM_LARRY_PRODUCTION.md` for the production handoff.

Existing production has uncommitted management-log/auth improvements outside
Git main. Deployment must preserve those changes and apply this module's small
app/navigation/feature registrations to the live files; do not broadly replace
the production checkout with this worktree or run the broad deployment script.

Source configuration is `INQUIRY_EMAIL_SOURCES`, a JSON list of objects containing
`id`, `mailbox`, `brand`, `subject_prefixes`, and optional exact sender addresses
in `senders`. With no override, the implementation uses the mailbox confirmed by
Richard and the observed/specification format `New stay inquiry EH-`, sent by
`yunhang.chen@gmail.com`. An empty list disables intake. Additional brands or
patterns require explicit configuration rather than a broad scan of business mail.

## Larry intake contract

Browser calls require the usual approved session, CRM feature grant and the
page's CSRF token. Existing legacy full-access API keys can be used by the worker;
restricted Guest Issues keys cannot. API keys cannot administer CRM settings.
The bridge reads the raw key only from `INQUIRY_CRM_API_KEY` and follows no HTTP
redirects. Do not reuse or export the browser session as a worker credential.

```json
{
  "source": "enchanted-havens-email",
  "mailbox": "larry@enchantedhavens.com",
  "message_id": "immutable-gmail-id",
  "thread_id": "gmail-thread-id",
  "internet_message_id": "<original@example.invalid>",
  "in_reply_to": null,
  "received_at": "2026-10-20T17:00:00Z",
  "sender": "Enchanted Havens <yunhang.chen@gmail.com>",
  "subject": "New stay inquiry EH-EXAMPLE1 from Example Guest",
  "body": "Original full email text, unchanged",
  "extraction": {
    "reference": "EH-EXAMPLE1",
    "fields": {"guest_name": "Example Guest", "email": "guest@example.invalid"},
    "review_reasons": ["Dates not supplied"]
  }
}
```

`GET /inquiries/api/worker-context` returns allowed extraction fields, source
configuration and real property/tag IDs. Only explicit fresh guest content can
be extracted. Internal notes/status/assignment are rejected in email extraction.
Leave unknowns absent; preserve all original source text. Email content is data,
never permission to execute an action or send a response. A new trip in an
existing thread needs its own reference and an explicit `new_inquiry: true` after
Larry reviews the evidence. Otherwise the ambiguous message enters failure review.

Messages from participants other than the known guest are retained for context
and review; they cannot overwrite the guest's fields. Unmatched email and messages
outside the configured source format are ignored. Parsing failures return
`processing_status: failed` with a durable message ID; do not treat HTTP 200 alone
as evidence of successful processing. Replays return `replayed: true`.

Bridge commands (full Gmail API/connector MIME messages, never search snippets):

```sh
python scripts/inquiry_crm_client.py --base-url https://yourcottoncandy.com context
python scripts/inquiry_crm_client.py --base-url https://yourcottoncandy.com prepare \
  --gmail-file /private/path/messages.json --output /private/path/packet.json
python scripts/inquiry_crm_client.py --base-url https://yourcottoncandy.com ingest \
  --packet /private/path/packet.json --results /private/path/results.json
```

Prepare writes a new mode-0600 packet and never overwrites an existing one.
Larry returns `{"results":[{"message_id":"…","source_hash":"…","extraction":{…}}]}`.
Known internal tests/unrelated messages use an explicit `skip_reason` instead.
The complete result set and original source hashes are validated before any
write. Full-text packets contain guest data: keep them outside Git, retain only
as needed for retries, and remove them after verified ingestion. The original
source remains available through the secure Gmail reference in the CRM.

## Future source/channel backlog — retained from comment 710

These categories are not release-one integrations. Each future implementation
must have an owner, verified existence/access, acquisition mechanism, capture
path, CRM fields, permissions, historical coverage, and an idempotent sample test.
Until investigated, owner is unassigned, setup is unverified, readiness is not
tested, available fields are unknown, and next action is VERIFY. A channel must
not be called missing solely because no connector has been found.

| # | Source/channel | Acquisition → capture → CRM / dependency to verify |
|---|---|---|
| 1 | Website contact/availability/quote/group/event forms | Existing website → inquiry email → this intake; website/email existence verified, runtime connection pending; owner unassigned; next CONNECT; name/contact/reference/stay fields observed; live test pending |
| 2 | Direct booking engines | Booking/abandonment/rebooking flow → authorized engine records; include guests who never submitted inquiries |
| 3 | Hostaway and OTAs | Reservation/inquiry history → authorized PMS/channel data; distinguish real from relay contact details |
| 4 | Business email | Guest email/quote follow-up → approved shared/reservations inboxes and archives; release one uses Larry's mailbox only |
| 5 | Calls/messaging/chat | Staffed phone/WhatsApp/SMS/live chat → supported connector or quick staff entry |
| 6 | Social accounts | Published account discovery → supported DM intake; followers remain audiences |
| 7 | Advertising lead forms | Approved campaign and landing page → lead form and authorized connector |
| 8 | Voluntary marketing signups | Offer/guide/availability alert → signup form with recorded preferences; booking contact does not imply consent |
| 9 | Travel/venue directories | Directory listing discovery → referred traveler inquiry and source link |
| 10 | Guest-provided stay information | Check-in/registration/concierge/surveys → guest-provided details; additional travelers register themselves |
| 11 | Historical records | Approved PMS/spreadsheet/older CRM/email archive → provenance-preserving import |
| 12 | Gift cards/repeat programs | Purchase/registered recipient/return-guest request → opt-in program intake |
| 13 | Personal referrals | Guest/staff/owner/creator introduction → referral form/link and attribution |
| 14 | Travel/local partners | Partner relationship → referred-inquiry link/form; do not assume access to customer lists |
| 15 | Corporate/group organizers | Organization outreach → organizer inquiry; distinguish intermediaries from travelers |
| 16 | Communities/events/offline | Event/QR/public travel request → landing page or staff capture; anonymous audiences are not leads |
| 17 | External providers/public business prospects | Authorized marketplace/licensed campaign/public organization → qualified inquiry; business contact alone is not travel intent |

## Verification

All tests use synthetic guests and an explicitly disposable local database whose
name starts with `codex_inquiry_qa_`. They refuse another database/remote host.

```sh
DATABASE_URL=postgresql://localhost/codex_inquiry_qa_813 \
INQUIRY_QA_DATABASE_URL=postgresql://localhost/codex_inquiry_qa_813 \
OPENAI_API_KEY=qa-not-a-real-key SECRET_KEY=qa-only \
  /path/to/venv/bin/python -m pytest dashboard/inquiries/tests dashboard/auth/tests/test_features.py -q
node --check dashboard/static/js/inquiries.js
```

The UI test covers desktop and 390px mobile, all 12 columns, full inline-note
content, the side panel, Pacific/UTC conversion, guest reply acknowledgement,
filter/clear behavior, unsaved edits and source HTML escaping. Screenshots are
written to `/tmp/ticket-813-qa/` and contain synthetic data only.
