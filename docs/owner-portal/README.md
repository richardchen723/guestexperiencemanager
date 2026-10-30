# Ticket 847: data audit and owner access

This first implementation establishes access for the approved visual owner dashboard. It does not publish financial figures, forecasts, owner statements, or guest information. The owner landing page uses the approved warm neutral/teal styling and shows only explicitly assigned properties, with a truthful preparation state for performance and statements.

## Boundary

owner_portal.app:create_app is a separate Flask app, started by wsgi_owner.py. It never imports the staff app, staff sessions, Hostaway source models, or bookkeeping routes. It has a separate Google OAuth client, signing secret, __Host-owner_portal cookie, OS service user and database login. The approved public URL is https://yourcottoncandy.com/owners. Nginx sends that path to the separate service; OwnerMount handles a fixed path prefix without trusting forwarded-prefix headers. Path hosting shares a browser origin with the staff app, while authentication, process and database boundaries remain separate. The factory does not run migrations. Startup rejects a database credential with superuser/role-administration privileges or readable/writable business tables outside the owners schema.

Owner accounts represent households or companies. Individual identities are keyed by verified Google subject, never by a staff role or email lookup. Memberships give separate performance and finalized-statement permissions. Property grants define the exact physical property and reporting interval [from, until). An open-ended grant has no until. Reporting history is independent of invitation acceptance or sign-in time; prior-year comparison reads must satisfy the same interval checks. Revoking membership ends existing sessions; account, membership, property and grant status are checked when serving data. A new invitation can explicitly restore revoked membership.

Physical properties may have several alternative listings, all verified by staff to describe the same unit. Each source listing can map to only one property. Bundle listings are deliberately unsupported in this phase and must remain unmapped until allocation rules are implemented. A portfolio label is only a display grouping. Neither operating/bookkeeping portfolios nor Sales CRM relationships grant access. No existing CRM contact is automatically enrolled and the internal owner superadmin role is never used for property owners.

/api/context returns a small explicit allowlist: accounts, permissions, property display names, currency, timezone, and reporting bounds. It does not return listing IDs, emails, Google subjects, source reservation records, guest identities, messages, financial source rows, or internal staff notes. There are no metric, statement download, or internal app endpoints in this app yet. Future handlers must call authorize_property before querying prepared owner data; totals, comparisons, caches and downloads must respect the authorized property set and interval. Published statements will additionally require frozen recipient/property scope and immutable publication versions, not merely the membership flag.

## Read-only source audit

Run with a deliberately selected source credential. It opens a repeatable-read, read-only transaction with a per-query timeout; it never initializes the staff app or reads guest/message fields, raw calendar payloads or statement contents.

~~~bash
OWNER_AUDIT_DATABASE_URL="$READ_ONLY_SOURCE_URL" python -m owner_portal.audit --environment 'Name and freshness of source snapshot' --as-of 2026-10-30 --output /private/path/owner-audit.json
~~~

The output file must not already exist and is created with mode 0600. Keep actual source evidence private, outside version control. It reports listing metadata gaps, date/status quality, per-channel financial field presence (unknown and zero remain distinct), potential duplicate source references/shared addresses, monthly history coverage, latest calendar coverage per listing, independent portfolio mappings, bookkeeping revision availability, and owner/CRM schema availability. A shared address is a review candidate, not proof of duplicate units.

The finance presence section counts new, modified, and confirmed reservations overlapping the audit window. These are candidates for review, not a certified financial dataset; customIcal, zero-priced rows, ambiguous bundle mappings, refunds, taxes and deposits still require source interpretation. Monthly coverage uses arrival month and is not a stay-night revenue/occupancy calculation. Missing source tables are reported as unavailable. An incompatible existing table fails the audit rather than producing misleading partial totals.

The first local snapshot audit is saved in ignored output/owner-847-qa/ for review. It is not production verification. Before enabling performance: verify canonical accommodation revenue per channel against source evidence; hydrate or explicitly validate timezone/currency; approve physical-unit/bundle mapping; classify unavailable nights; and expand/sustain calendar history. The recently merged finance-sync and occupancy fixes remain unchanged by this work. Finalized owner statements still need a separate publication workflow from approved bookkeeping revisions. Forecasts and honest prior-year comparisons remain later milestones.

## Explicit setup

1. Migrate with an administrator credential: OWNER_ADMIN_DATABASE_URL=... python -m owner_portal.cli migrate. This creates only the additive owners schema, tables and indexes. It creates no owner identities, grants, or invitations. Repeating it is safe for this initial schema; future schema changes require explicit migrations.
2. Have the database administrator create a dedicated, non-inheriting login with no memberships, superuser, database/role creation, source-table rights, or executable privileged functions. Generate its grants with python -m owner_portal.cli runtime-sql --role owner_portal_web and apply the reviewed SQL. Do not supply staff database credentials to the owner service. The owner runtime can update authentication records but cannot grant property access or read source business tables. The startup check is a guard against table privilege mistakes; DBA review must also cover role memberships and executable security-definer functions.
3. Configure the owner service independently:

   ~~~text
   OWNER_PORTAL_DATABASE_URL=<restricted PostgreSQL login>
   OWNER_PORTAL_SECRET_KEY=<independent random secret, at least 32 characters>
   OWNER_PORTAL_ORIGIN=https://yourcottoncandy.com
   OWNER_PORTAL_PATH_PREFIX=/owners
   OWNER_PORTAL_LOGIN_ENABLED=false
   OWNER_GOOGLE_CLIENT_ID=<dedicated OAuth client>
   OWNER_GOOGLE_CLIENT_SECRET=<dedicated OAuth secret>
   ~~~

   Configure the exact Google redirect URI as https://yourcottoncandy.com/owners/auth/google/callback. Keep OWNER_PORTAL_LOGIN_ENABLED=false until the real owner OAuth client is configured and verified; the public page then explains that sign-in is being prepared, all authentication handlers reject requests, and staff invitation creation is disabled. Set the same readiness flag on both services when activating it. The app uses authorization code + PKCE, browser-bound single-use state, verified signed ID tokens, nonce, audience/issuer checks, and verified email matching the invitation. It binds later sign-ins to the Google subject. Tokens expire server-side after 12 hours; sign-out revokes the session. See [Google OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect) and [ID-token validation](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token).

4. Create a dedicated hostaway-owner system user and install deployment/hostaway-owner-portal.service with an isolated, root-readable /opt/hostaway-messages/owner-portal.env. The service binds only 127.0.0.1:5011 and cannot read staff environment files, guest storage, or release backups. Install deployment/nginx-owner-http.conf in the Nginx http context and the proxy/locations snippets at the paths documented in those files. Include the locations snippet in the existing HTTPS server; preserve the original /owners path and Host header. Test Nginx before reload. Owner OAuth endpoints are rate limited and owner access logs exclude query strings and bodies. The owner service omits Gunicorn access logging so OAuth codes are not logged there. Apply normal retention to consumed/expired login flows and sessions; preserve access audit events.
5. After migration, set OWNER_ACCESS_ENABLED=true and the same OWNER_PORTAL_ORIGIN, OWNER_PORTAL_PATH_PREFIX and OWNER_PORTAL_LOGIN_ENABLED on the staff app. Approved human admins see **System → Owner access**. The setup feature is disabled by default and rejects API keys, service identities, unapproved/inactive users, and missing CSRF tokens.
6. Create an owner account, register verified physical properties, set each reporting grant, and create one invitation per person. Links expire after seven days; only their hashes are stored. The raw link is shown once, with its token in the URL fragment so it does not enter HTTP access logs. The owner page removes the fragment and stages the hash before Google sign-in. Send the link only to the intended verified contact address through an approved channel; Google email verification alone is not proof of current control of a third-party email address. This implementation generates links but sends no email. A replacement invitation invalidates previous pending links. Use the staff read-only preview and revoke controls to verify access. Preview is audited and never creates an owner session.

Activation is explicit: configure/test the real OAuth client, restricted role, proxy routing and a reviewed pilot mapping before enabling owner sign-in. No owner assignments or invitation deliveries are automatic. Roll back the release-owned code/configuration, disable the staff flag and stop the separate owner service; preserve the schema and audit records. Source operational evidence and deployment journals stay outside this public repository.

## Verification

Use only a disposable local PostgreSQL database whose name starts with codex_owner_qa_. The fixture truncates **only the owners schema** and temporarily creates synthetic source tables. Never point it at a shared development or production database. The QA administrator must be able to create the dedicated test login.

~~~bash
OWNER_QA_DATABASE_URL=postgresql://qa_admin@127.0.0.1:5847/codex_owner_qa_847 python -m pytest owner_portal/tests -q
OWNER_BROWSER_QA=1 OWNER_QA_DATABASE_URL=postgresql://qa_admin@127.0.0.1:5847/codex_owner_qa_847 python -m pytest owner_portal/tests -q
~~~

Browser checks use installed Playwright Chromium, local HTTPS, synthetic identities and the real owner templates. Staff browser checks use the real setup page with an isolated staff shell and authentication fixture. Screenshots go to ignored output/owner-847-qa/. Automated Google-flow tests exercise signature verification with synthetic RSA-signed tokens and mocked transport; they do not validate live Google client configuration or deliver invitations.
