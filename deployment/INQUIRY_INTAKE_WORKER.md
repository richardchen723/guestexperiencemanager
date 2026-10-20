# Ticket 813: production intake worker

Worker: `scripts/inquiry_intake_worker.py`; systemd unit:
`deployment/hostaway-inquiry-intake.service`. This is separate from the assistant
Gmail event automation and requires no Mac after activation.

The owner approved Gmail read-only authorization for `larry@enchantedhavens.com`
and storage of that authorization and the existing Cotton Candy key on the
verified production server on 2026-10-20 at 12:26:55 UTC. No other mailbox,
send scope, domain-wide delegation, service purchase or IAM expansion is covered.

## Consent and credential binding

Use the existing production Google OAuth client and its registered callback
`https://yourcottoncandy.com/auth/google/authorized`. No new client, redirect URI,
connector token export, Drive token reuse or browser-session export is needed.

The owner opens `https://yourcottoncandy.com/inquiries/gmail/connect` while signed
in as the Cotton Candy owner, then chooses the Larry mailbox in Google consent.
The page requests only `https://www.googleapis.com/auth/gmail.readonly`, with
offline access and without combining previously granted scopes. Gmail metadata
scope cannot read message bodies. References:
[Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes),
[server OAuth and offline access](https://developers.google.com/identity/protocols/oauth2/web-server).

The callback verifies owner session, CSRF state, ten-minute expiry, one-use nonce,
exact granted scope, refresh-token presence and the Gmail profile's exact mailbox.
It stores the refresh credential in a mode-0600 file within the configured private
mode-0700 `INQUIRY_GMAIL_AUTHORIZATION_DIR`, outside the app checkout. Access tokens
stay in memory. Normal sign-in and Drive callbacks retain their existing handlers.
Denied consent, wrong mailbox or broader scopes never become worker credentials.

After a successful callback, a server administrator validates the staged file's
account/scope metadata and atomically binds it as root-owned mode-0400
`/etc/hostaway-inquiry-intake/larry-gmail.json`, in a root-owned mode-0700 directory.
Bind the existing Cotton Candy key separately as mode-0400 `crm.key` through the
already authorized secret mechanism. Never print or put either credential in
arguments, source, logs, Git, a browser session or chat. Systemd `LoadCredential`
supplies private runtime copies to the worker. No raw secret belongs in `.env`.

If Google blocks consent, the Gmail API is disabled, an account policy intervenes,
or the existing client cannot request this scope, stop and report the exact page
or fixed failure code. Do not bypass prompts or create another client implicitly.
The owner may need to complete Google account authentication. A testing-mode
OAuth project's token lifetime must be checked before claiming permanent access.

## Activation

1. Preserve live changes; deploy only this worker, consent module, the tiny
   `register_inquiry_routes` registration, documentation and disabled service unit.
   Back up the old routes and record hashes. Do not deploy main over production.
2. Create the private authorization staging directory outside Git, owned by
   `hostaway`. Add only its path as `INQUIRY_GMAIL_AUTHORIZATION_DIR` and a narrow
   `ReadWritePaths` allowance to the dashboard service. Restart only the dashboard
   for this registration; verify existing health and anonymous API denial.
3. Complete the owner consent above, then bind both credentials. Validate the
   new Gmail credential using profile only and validate the CRM key with
   `GET /inquiries/api/worker-context`. Neither check imports mail.
4. Verify the service unit, install it and start/enable only
   `hostaway-inquiry-intake.service`. Its interval is 60 seconds. Do not start it
   without the verified mailbox credential and CRM key.
5. Inspect `systemctl is-active hostaway-inquiry-intake` and, as `hostaway`, run:

   ```sh
   /opt/hostaway-messages/venv/bin/python \
     /opt/hostaway-messages/app/scripts/inquiry_intake_worker.py status
   ```

The activation time is created only after live context and exact Gmail profile
checks pass. Startup reports older matching message IDs separately as backlog
candidates and never imports them. The state database is
`/var/lib/hostaway-inquiry-intake/state.sqlite3`; keep it and its WAL private and
persistent. Never reset it to replay history. No fake production leads are needed.

## Recovery and limits

Every cycle reads live worker-context, then searches configured initial senders
and subject prefixes. New initial messages must have Gmail internal receipt time
at or after activation. Scans overlap five minutes, advance their checkpoint only
after full pagination and durable capture, and reconcile from activation daily
to catch late indexing. Tracked Gmail threads are reconciled independently of
subject and unread state. Sent/draft/spam/trash and the verified internal test
EH-2902A0CA (including its known thread) are excluded.

SQLite uses WAL and FULL synchronization, a private directory and one worker
lock. Complete raw RFC822 originals, exact Gmail IDs and receipt timestamps are
durable before intake. The CRM body preserves the decoded original plain text;
the raw MIME also retains attachments and all original bytes. Oversized or
unsupported bodies are retained and blocked, never truncated. Originals are
not overwritten on retries. The CRM's returned `message_id` is a database ID;
it is stored separately from the original Gmail message ID.

An uncertain intake request retries the identical persisted payload. Transient
HTTP/transport failures use persisted exponential backoff with jitter, honoring
Retry-After (up to a day); normal backoff is bounded at an hour. Collection errors
are also durable. Unexpected ignored/failed/malformed results become blocked
review records even when HTTP is 200. Replies wait for their initial lead to be
processed. Crash recovery replays the same IDs, relying on CRM deduplication.

The worker uses the existing conservative parser. Explicit structured fields
update through the CRM conflict rules; unstructured natural-language replies
remain complete originals with review flags and attention. It does not call a
model, guess missing fields, set staff ownership/schedules, send replies or quotes,
or change prices. Pacific staff timezone and unset reminder/escalation/unassigned
settings remain controlled by the CRM.

To retry a blocked extraction, stop the intake service, review its original,
create a private JSON file containing only a corrected `extraction`, and run:

```sh
python scripts/inquiry_intake_worker.py retry \
  --message-id ORIGINAL_GMAIL_ID --extraction-file /private/corrected-extraction.json
```

Restart the same service. This changes only extraction, retaining original IDs
and source. Unsupported originals need administrator review before requeueing.
Do not repeatedly reset permanent failures automatically. Stop/disable only this
unit for rollback; retain its ledger and CRM audit history.

## Evidence needed for completion

Offline tests cover crash-after-accept replay, no duplicate source records,
changed-subject replies, partial-page outages, delayed mail, old-mail/test
exclusion, original-byte retention, persisted retry delays, status-body failures,
scope/account binding and consent replay/CSRF checks. Run them without network:

```sh
DATABASE_URL=postgresql://localhost/codex_inquiry_qa_worker_offline \
OPENAI_API_KEY=offline-only SECRET_KEY=offline-only PYTHONDONTWRITEBYTECODE=1 \
python -m pytest tests/test_inquiry_intake_worker.py \
  tests/test_inquiry_gmail_authorization.py -q -p no:cacheprovider
```

Before claiming live end-to-end completion, verify a genuine new inquiry and its
guest reply, then an exact replay: one lead, two original messages, no duplicate
activity, correct preserved staff edits and exact IDs. Report the lead URL,
receipt/import times, unit state, activation/last-sync timestamps and recovery.
Until those messages arrive, end-to-end verification remains pending.


## Authorized internal website test (2026-10-20)

Release `inquiry-test-isolation-20261020-r3` adds immutable test classification on
source messages and leads through two additive boolean columns, default false.
Only an administrator browser session or an existing full-access API key can
request `internal_test: true`. Failed-message retries preserve classification;
staff patching cannot change it, and test messages cannot merge into genuine leads.
The default lead list excludes tests. An explicit administrator test view at
`/inquiries/?record_scope=internal-tests` displays them; lead/conversion statistics
always exclude tests, even when a test is marked Booked.

A private `test-runs.json` beside the worker state registers a unique marker,
owner email and short receipt/collection window. Only a configured initial source
with matching registration receives the flag before intake. Unknown/expired test
markers are retained for review rather than imported into genuine statistics.
Retries keep the exact captured payload after the registration expires.

The owner-approved form test returned reference EH-A340F6FC. Its initial email
arrived at 13:49:13 UTC and was processed at 13:50:04 UTC as internal test lead 1.
Exact replay returned processed/replayed=true without changing its one message,
one activity or lead version. A worker-only restart preserved the activation
cutoff and processed ledger. Genuine lead statistics stayed zero. These results
are separate from the still-pending genuine inquiry and guest-reply validation.
The combined existing/new CRM, worker and authorization suite passed 84 checks
against a fresh local PostgreSQL test database and offline protocol mocks.

See `inquiry-test-e2e-20261020.json` for immutable IDs and original hashes, and
`inquiry-oauth-readiness-20261020.json` for the read-only durable-auth proposal.
Google External/Testing remains in effect; the current read-only authorization
is expected to expire around 2026-10-27 13:19:16 UTC. The project is not owned by
a Google organization. No publishing, audience, scope, IAM or legal-page changes
have been made. Internal business use alone does not establish Google's Internal
audience eligibility or a verified exemption.
