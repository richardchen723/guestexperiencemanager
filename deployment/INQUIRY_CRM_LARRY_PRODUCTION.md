# Larry: activate production Guest Inquiry CRM

The CRM for ticket 813 is deployed. Connect your continuously running production
email workflow to it. Deployment did not start an inbox watcher.

## Production connection

- Dashboard: https://yourcottoncandy.com/inquiries/
- Contract/configuration: `GET https://yourcottoncandy.com/inquiries/api/worker-context`
- Import: `POST https://yourcottoncandy.com/inquiries/api/intake`
- Authenticate with `X-API-Key` using your existing authorized Cotton Candy
  full-access credential. Production has an active key named Larry Key. The raw
  key was not retrieved during deployment. Read it only through your existing
  authorized secret mechanism; never print it or put it in chat, URLs or Git.
- If that credential is unavailable, report the exact missing access. A restricted
  Guest Issues key/adapter cannot call these routes. Do not broaden it or export
  a browser session to work around the restriction.
- Production server code: `/opt/hostaway-messages/app`.
- Optional bridge: `/opt/hostaway-messages/app/scripts/inquiry_crm_client.py`.
  It reads the API key from `INQUIRY_CRM_API_KEY`. Example read-only connection check:

```sh
/opt/hostaway-messages/venv/bin/python \
  /opt/hostaway-messages/app/scripts/inquiry_crm_client.py \
  --base-url https://yourcottoncandy.com context
```

Direct HTTPS calls are supported; the worker can run in your existing production
runtime. It must remain active independently of Richard's computer or this chat.

## Start processing new inquiries promptly

1. Read `worker-context` first. Use its current sources, fields and real property/tag
   IDs. Source is currently `enchanted-havens-email`; mailbox is
   `larry@enchantedhavens.com`; new-inquiry subjects start `New stay inquiry EH-`;
   configured sender is `yunhang.chen@gmail.com`. Match configured values exactly.
2. Check new eligible mail every 60 seconds while push delivery is being arranged.
   Use an overlapping lookback, durable cursor/job records and Gmail message-ID
   deduplication. Do not rely on unread state or label changes. Record activation
   time and report older backlog separately instead of silently mass-importing it.
3. Track inquiry threads and process subsequent guest replies even if their sender
   or subject changes. Download complete original messages, not search snippets.
   Retain Gmail message/thread IDs and actual receipt timestamps. A Pub/Sub
   notification ID is not a Gmail message ID.
4. Extract only explicit fresh guest information. Leave unknowns absent. Use
   property/tag IDs only for confident matches, choosing one for the lead. Include
   review reasons for ambiguity. Preserve staff corrections, assignment, status,
   internal notes and follow-up schedules. Preserve stated budget currency;
   defaults for new website currency settings/quotes/transactions remain USD.
5. Submit original source plus extraction to intake. Preserve originals unchanged.
   HTTP 200 alone is insufficient: inspect `processing_status` and `replayed`.
   `processed` is successful; `ignored` must have an understood scope reason;
   `failed` needs a durable failure record and review/corrected retry using the same
   original IDs. Retry temporary transport/server failures with backoff. Retain
   enough durable state to recover after restarts without losing mail.
6. Skip known internal tests, including EH-2902A0CA, rather than creating genuine
   leads. Do not send guest replies, quotes or pricing changes as part of this job.

Minimum intake shape (illustration only; never submit this example):

```json
{
  "source": "enchanted-havens-email",
  "mailbox": "larry@enchantedhavens.com",
  "message_id": "ORIGINAL_GMAIL_MESSAGE_ID",
  "thread_id": "ORIGINAL_GMAIL_THREAD_ID",
  "internet_message_id": "<original@example.invalid>",
  "in_reply_to": null,
  "received_at": "2026-10-20T12:30:00Z",
  "sender": "Enchanted Havens <yunhang.chen@gmail.com>",
  "subject": "New stay inquiry EH-EXAMPLE1 from Example Guest",
  "body": "COMPLETE UNCHANGED ORIGINAL EMAIL TEXT",
  "extraction": {
    "reference": "EH-EXAMPLE1",
    "fields": {"guest_name": "Example Guest", "email": "guest@example.invalid"},
    "review_reasons": ["Dates not supplied"]
  }
}
```

The bridge supports full Gmail API/connector MIME JSON with `prepare`, then
`ingest` after extraction. It validates complete source hashes before writes.
Use private mode-0600 packet/result files outside Git; remove transient packets
after verified processing. See the deployed `dashboard/inquiries/README.md`.

## Prefer Gmail push when supported

Gmail supports `users.watch` through Google Cloud Pub/Sub. It does not POST full
emails directly to the CRM intake endpoint. Use a Pub/Sub pull worker or a separate
authenticated push handler that durably queues work, calls Gmail `history.list`
from a saved cursor, retrieves messages, extracts fields, then calls CRM intake.
For push, validate the signed Google JWT, audience and service account identity;
acknowledge only after durable acceptance. Use your existing approved Gmail API
credentials; do not extract connector tokens. The current Gmail connector alone
does not expose watch/history/subscription setup.

Renew watches daily, track their returned expiration, page history completely,
handle expired history cursors with scoped resynchronization, and keep periodic
reconciliation for missed notifications. Notifications are usually fast but not
guaranteed. Do not claim a webhook is active until delivery and recovery are proven.
Report missing OAuth scope, Cloud project/topic/subscription access or supported
worker capability precisely if setup cannot be completed.

Google references: [push](https://developers.google.com/workspace/gmail/api/guides/push),
[sync](https://developers.google.com/workspace/gmail/api/guides/sync),
[authenticated push](https://docs.cloud.google.com/pubsub/docs/authenticate-push-subscriptions).

## Verification and report back to Richard

Verify the first genuine new inquiry, its guest reply, and an exact replay in
production. Confirm one lead, both immutable messages, correct reply updates and
no duplicates; provide the CRM lead URL and receipt-to-import times. Until genuine
mail arrives, report that end-to-end verification is pending. Do not seed fake
guests or known tests into production conversion statistics to claim completion.

Report the always-on job/service identifier, active polling interval or verified
watch/subscription details, last successful sync time, failure/recovery behavior,
and any blocker. Staff timezone is `America/Los_Angeles`. Leave reminder,
escalation and unassigned timing unset until Richard specifies them. Those timing
settings are separate from the 60-second email collection interval.
