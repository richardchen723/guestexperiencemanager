# Owner Sales CRM — ticket 843

Implemented as a separate native workspace at `/sales/`, integrated with main while remaining compatible with the existing #834 Guest Inquiries deployment. Guest inquiry records are not copied or repurposed. The app's authentication, feature grants, shell, API credentials, database connections, and operational listing directory are reused.

## Implemented scope

- Searchable, paginated opportunity table; responsive cards; salesperson, market, source, outcome, creation-date and attention filters; a saved filter view per browser/user; permission-controlled CSV export.
- Eight-stage pipeline with independent column pagination. Stage changes are explicit reviewed commands, rather than drag-and-drop that could bypass requirements. Nurture, lost and disqualified remain distinct dispositions.
- Owner accounts, contacts, prospect properties, and opportunities are separate records. Multiple properties and decision makers can share an opportunity; properties have owner/contact relationships. Existing records can be linked rather than copied. Hostaway IDs are optional links to actual operational listings.
- Detail page with next action, activity timeline, tasks and appointments, qualification, discovery, assessment, immutable proposal versions, agreement evidence, and operations handoff.
- Manual capture; CSV preview and atomic commit; immutable source IDs; authenticated agent intake with retained original evidence and a failed-intake review queue. Duplicate suggestions never merge automatically.
- Human qualification decisions, evidence references, separate annual rental and management revenue, optional acquisition cost, and all commercial amounts in USD using Decimal values.
- Channel-specific permission history, expiry, evidence and DNC review. Opt-outs survive merges. Changing a contact endpoint invalidates an old permission. Releasing an opt-out/restriction requires Sales management access.
- Every proposal version requires manager approval before recording delivery. A signature freezes the selected proposal and actual signed properties/signatories. Unsigned properties require an explicit split or exclusion reason. Agreement Signed and Onboarding are separate board stages; accepting and completing a handoff are explicit operations actions.
- Cohort reports distinguish owners, opportunities, properties, signed agreements, estimated management revenue, and won management revenue. Unknown amounts remain unknown. Test records are excluded.
- Durable reminder/escalation/nurture queues, plus an adapter and runner for the shared #817 notification framework. No external delivery is enabled by installing this feature.

## Workflow and integrity

New intake can be incomplete. Open opportunities beyond New need an eligible assigned salesperson and one primary next action with a due time. Completing that action requires a replacement while the opportunity is active. Supporting tasks and appointments remain separate.

Outreach requires approved qualification and a recorded permitted outbound attempt. Discovery requires owner-needs notes; Assessment requires a reviewed assessment; Negotiation requires the current approved proposal to have been sent. Reopening, closing, and backward moves require a reason. Nurture requires a future revisit time. Won records remain won while onboarding progresses.

Mutations require a UUID `command_id`. Repeating the same actor/command/payload replays the original result; reusing the ID for different data is rejected. Opportunity and shared-record edits check a version. Transactions and a PostgreSQL advisory lock serialize commands, with row locks and database uniqueness protecting key invariants. Business history, proposal versions, actions and signed agreements are append-only at the database level.

Only an approved browser user with `sales` can operate the workspace. `sales_management` enables approvals, merges, settings and failed-intake review; `sales_export` enables export. Existing owner/admin roles retain the app's normal full-feature behavior. Scoped agent keys support `sales:read` and `sales:intake`; their issuer must retain Sales access. Agent keys cannot make qualification, permission, commercial, or handoff decisions. Audit records identify the actual API key, rather than assigning every automated change to Larry.

Browser mutations require a session CSRF token. Responses are private/no-store. Text is escaped in the UI, links are HTTPS-validated, JSON/CSV size is bounded, imports require a signed expiring preview proof, and spreadsheet formula prefixes are escaped on export.

Time is stored in UTC and presented in `America/Los_Angeles` by default. Local input rejects nonexistent DST times and requires an explicit occurrence when an hour repeats. Reminder intervals and business guidance remain unset until configured by the team.

## Deployment

1. Take the normal database backup. On a production checkout with additional deployed changes, apply only the reviewed Sales file overlay, preserving those changes and checking every original file hash before installation.
2. With the explicit target `DATABASE_URL`, run `python -m dashboard.sales.cli migrate`. This creates the additive `sales` schema, indexes, settings row and immutable-history triggers. It creates no sample leads or feature grants and does not modify Guest Inquiries.
3. Deploy/restart the app. Grant `sales` to the pilot team through Team access; grant management/export capabilities only to the intended people. Verify the pilot assignees appear in the directory.
4. Create acquisition sources and configure agreed qualification, assignment, commercial and onboarding guidance. Timing fields are optional and disabled while blank. All proposals require approval regardless of guidance.
5. Pilot manual capture, a reviewed CSV, a contact permission review, a follow-up, a proposal revision, a partial signature, and operations acceptance. Confirm the report denominators and mobile workflow with the team.
6. Install `deployment/hostaway-sales-reminders.service` and `.timer` in `/etc/systemd/system`, reload systemd, and enable/start `hostaway-sales-reminders.timer`. It runs `python -m dashboard.sales.cli reminders` every five minutes without a browser and sends nothing. A run is replay-safe; subsequent runs suppress superseded reminders. Reminder timing remains disabled while its settings are blank.

Disable access grants and stop the Sales job to roll back exposure. Preserve the additive schema and its history; do not drop business data to roll back the UI.

### Shared notifications

The current base branch contains the legacy WhatsApp module, not the full #817 outbox/planner/worker implementation. The new Sales adapter has been contract-tested against the #817 worktree without copying its unrelated changes into this branch. Install/merge that framework and run its documented migration before using shared capture.

- `python -m dashboard.sales.cli capture` generates reminders and captures pending Sales notices into the shared outbox. It never sends.
- `python scripts/run_sales_notifications.py` composes existing Tickets/Procurement definitions and policies with Sales, generates/captures notices, and plans previews. It never sends by default. Use it as the Sales-aware replacement entry point, not alongside a planner that does not recognize Sales events.
- The runner requires `DATABASE_URL` and `NOTIFICATIONS_PUBLIC_ORIGIN`. Live mode additionally requires `--live`, `NOTIFICATIONS_CAPTURE_ENABLED=1`, `NOTIFICATIONS_DELIVERY_ENABLED=1`, the shared rollout file with an explicit start boundary, allowed event names and destinations, and the existing bridge credentials/configuration. The token file must be mode 0600. No live configuration or destination was created by this implementation.
- Sales event names are `sales.deal.created`, `assigned`, `reply`, `stage_changed`, `handoff`, `handoff_updated`, `reminder`, `escalation`, `unassigned`, and `nurture` (each with the `sales.deal.` prefix).
- Sales notifications are restricted to verified private recipients with current Sales access and personal notification consent. Group delivery is deliberately denied pending an explicit Sales audience policy. No hard-coded fallback person is used. Missing responsibility remains blocked and visible in delivery history.
- Revalidation checks task version/status/due time, current assignee, current permissions, opt-out, handoff state and record outcome. Rescheduled/completed tasks and closed opportunities suppress stale alerts. Shared retry/reconciliation semantics are retained. Preview deliveries are not promoted to live deliveries.

## API outline

The UI consumes `/sales/api/bootstrap`, `/leads`, `/pipeline`, `/tasks`, `/reports`, `/directory/<kind>`, and `/leads/<id>`. Individual mutations use the opportunity version and their explicit action endpoint: `/activities`, `/tasks`, `/relationships`, `/permissions`, `/qualification`, `/assessment`, `/transition`, `/proposals`, `/proposal-actions`, `/sign`, and `/onboarding`.

An agent intake request to `POST /sales/api/intake` includes:

```json
{
  "command_id": "b3a58685-9852-4553-913f-72975424b8e0",
  "source_id": 1,
  "external_id": "stable-source-record-id",
  "original": "Original source text retained verbatim for review",
  "lead": {
    "title": "Owner portfolio",
    "account": {"name": "Example owner"},
    "contacts": [{"name": "Example contact", "email": "owner@example.invalid"}],
    "properties": [{"name": "Example cabin", "market": "Whidbey Island"}],
    "source_reference": "https://example.invalid/source"
  }
}
```

Use a real configured source ID, a stable external ID, and a fresh UUID per logical command. Retrying the same source ID/external ID with different original data is a conflict requiring review. Intake records extraction failures without discarding the evidence. A failed extraction is a committed `status: failed` response, not a partially created opportunity.

CSV columns: `title,owner_name,contact_name,email,phone,property_name,address,market,source_reference`. Maximum 200 rows / 500 KB; one opportunity per row. Link additional records from the detail page. CSV import does not deduplicate a household based only on a shared email.

## Validation and follow-on scope

Tests use an explicitly named, disposable local PostgreSQL database (`codex_sales_qa_*`). Run:

```sh
SALES_QA_DATABASE_URL="$DATABASE_URL" python -m pytest dashboard/sales/tests dashboard/auth/tests -q
```

The database must be localhost and its name must start with `codex_sales_qa_`; production URLs are rejected by the test fixture. The optional shared-framework integration test runs when #817 is installed. While testing a separate checkout, `SHARED_NOTIFICATIONS_QA_PATH` may point to that checkout's `dashboard` directory; it only extends the test import path.

Coverage includes portfolio relationships, unknown-money reporting, stage gates, stale writers, concurrent replay, missing next actions, source immutability, opt-out-preserving merges, endpoint permission changes, DNC/expiry, append-only proposals, approval, partial wins, onboarding, CSV proof/replay, failed intake retention, scoped API access, CSRF, DST, reminder supersession, task filters, and shared notification capture/planning/revalidation. Browser checks cover the real desktop table/pipeline/detail, activity save, adding a second property, stage change, and mobile detail/control layout.

The earlier plan explicitly left external integration work for follow-on tickets: Gmail send/thread synchronization, Google Calendar synchronization, dialer selection, PandaDoc/e-sign webhooks, automatic website feeds, and agent enrichment jobs. This release provides manual workflows and intake contracts for those integrations. Appointment tasks do not create calendar events, recording proposal delivery does not send mail, and recording a signed agreement does not execute a signature.

Numerical qualification weights, commercial thresholds, assignment rules, and reminder timing are business decisions still awaiting team configuration. No guessed defaults or live provider calls have been introduced.
