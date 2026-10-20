# Guest Inquiry CRM production release — ticket 813

Published October 20, 2026, verified healthy at 12:10:19 UTC.
Production: https://yourcottoncandy.com/inquiries/

Runtime source is commit `b82ff0f` on `codex/ticket-813-guest-inquiry-crm`.
Fifteen files were deployed: the CRM module, template, CSS/JS, intake client,
API documentation, and small registrations in the live app, feature catalog and
navigation. Shared-file patches were applied to the current production versions;
existing archive, management-log and authentication changes were preserved.
Production Git was not reset, pulled, committed or pushed.

The five additive `inquiries` tables are guests, leads, messages, activities and
settings. Initialization used the existing application startup database lock.
Timezone is America/Los_Angeles; reminder/escalation/unassigned intervals remain
NULL. No leads/messages, staff grants, API credentials, inbox watchers, recurring
jobs or guest-facing messages were created. An existing active full-access Larry
Key was confirmed by metadata only; its raw value was not accessed.

Validation: 66 tests passed against a local staging overlay of current live code,
including desktop/mobile browser acceptance, concurrency/idempotency and auth
regressions. Authenticated server smoke checks passed for CRM page/context/list,
default settings, denied anonymous/ungranted access and rejected invalid writes.
Existing Daily management workspace returned 200. Public HTTPS health and assets
were checked. Richard's existing approved production browser loaded the CRM and
confirmed that selecting enchanted havens reduces 58 properties to its 7 members.
No synthetic records were written to production.

## Rollback

Backup: `/opt/hostaway-messages/backups/inquiry-crm-20261020-r1`.
`manifest.json` records every path and before/after SHA-256. Existing files are
under `files/`; `before: null` identifies newly introduced files. The installer
automatically restored files and restarted the dashboard on deployment failure;
this release completed successfully.

For a later rollback, first check that deployed files still match the recorded
release hashes; reconcile newer edits before restoring. Stop only
`hostaway-dashboard`, restore existing files from `files/`, remove newly added
runtime files listed in the manifest, preserve hostaway ownership, and restart
the same service. Leave all additive CRM tables and genuine source/audit data in
place. No DROP or reverse data migration belongs to this release.

The later documentation update is separately backed up under the release's
`documentation/` folder. It requires no service restart.

## Remaining activation

Larry must connect his always-on production inbox workflow to the deployed intake
endpoint and verify a genuine inquiry plus reply/replay. The application itself
does not poll Gmail or expose a Gmail push receiver. Until that verification,
mail intake remains unconnected. See `INQUIRY_CRM_LARRY_PRODUCTION.md` for the
copy/paste production instructions, cadence, payload, credential requirements,
Gmail push options and required verification evidence.
