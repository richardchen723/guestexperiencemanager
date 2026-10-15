# Cotton Candy API

This document describes the HTTP API surface for third-party integrations. Access is controlled by the scopes attached to each API key.

**Base URL**
Use the same host and port as the dashboard application (for example `https://your-host.example.com`).

**Authentication**
Send an API key with every request.

Headers:
- `X-API-Key: <your_api_key>`
- `Authorization: Bearer <your_api_key>`

Do not put API keys in query parameters or logs. API keys are displayed only
once when created and are stored as keyed hashes.

**Access profiles**

- `Full API` (`*`) preserves access to the existing dashboard and Brain APIs,
  including write operations. Existing keys are migrated to this profile.
- `Guest Issues · read only` (`guest_issues:read`) can call only the versioned
  Guest Issues endpoints. It is rejected from legacy read and write APIs.

Create a separate least-privilege key for AI-agent access instead of sharing a
full-access operator credential.

## API conventions

The `/api/v1/...` surface is the stable external contract. Older dashboard APIs
predate these conventions and retain their existing response shapes for
backward compatibility.

**Success envelopes**

- Collection: `{"data": [...], "pagination": {...}, "meta": {...}}`
- Resource: `{"data": {...}}`

**Versioned error format**

Errors from `/api/v1/...` are machine-readable:

```json
{
  "error": {
    "code": "invalid_parameter",
    "message": "per_page must be 100 or fewer",
    "details": {"parameter": "per_page"}
  }
}
```

Common status codes are `400` for invalid input, `401` for missing or invalid
credentials, `403` for insufficient scope, `404` for an unknown resource,
`429` for rate limiting, and `500`/`503` for server-side failures.

**Pagination and sorting**

- Versioned collections accept one-based `page` and `per_page` values.
- `per_page` defaults to 50 and cannot exceed 100.
- Collection responses include `total`, `pages`, `has_previous`, `has_next`,
  `previous_page`, and `next_page`.
- Sorts include an immutable ID tiebreaker, so repeated requests over unchanged
  data have deterministic ordering.

**Rate limiting and audit**

The Guest Issues API defaults to 60 requests per credential per rolling minute.
Set `GUEST_ISSUES_API_RATE_LIMIT_PER_MINUTE` to change the deployment limit.
Responses include `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and
`X-RateLimit-Reset`; `429` responses also include `Retry-After`.

Each authorized attempt records the API-key ID, required scope, method, path,
query-parameter names (not values), status, result count, rate-limit outcome,
time, and a one-way client fingerprint. Guest text and query values are not
copied into the audit ledger.

**Notes**
- File upload endpoints require `multipart/form-data`.
- Some endpoints return `401` if authentication is missing and `403` if access is denied.

## API Key Management (Admin)

- `GET /admin/api/api-keys`
  - List API keys (metadata only).
- `POST /admin/api/api-keys`
  - Create a new API key.
  - Full access body: `{"name": "Third Party Partner", "access": "full"}`
  - Guest Issues read-only body: `{"name": "AI issue reader", "access": "guest_issues_read"}`
  - Response includes the raw key once.
- `POST /admin/api/api-keys/{api_key_id}/revoke`
  - Revoke an API key.
- `DELETE /admin/api/api-keys/{api_key_id}`
  - Permanently delete an API key.

API key management endpoints require an approved browser session for the
configured owner email. API-key authentication cannot be used to manage API
keys. Use the owner-only `/admin/api-keys` page to manage credentials. A new
raw key is displayed once when it is created; existing keys expose only their
prefix and metadata.

## Guest Issues API v1

Guest Issues provides a read-only, PII-minimized contract for authorized AI
agents. It never returns guest names, email addresses, phone numbers, postal
addresses, raw message content, sender information, API credentials, or source
URLs. Reservation, conversation, review, listing, ticket, and user IDs are
internal references only.

### List Guest Issues

`GET /api/v1/guest-issues`

Requires `guest_issues:read` or `*`.

Query parameters:

- `listing_id`: exact listing/property ID.
- `portfolio`: exact canonical portfolio name, case-insensitive.
- `status`: one or more comma-separated operational states:
  `need_attention`, `scheduled`, `in_progress`, `stuck`, `resolved`.
- `priority`: one or more comma-separated values: `Critical`, `High`, `Medium`,
  `Low` (case-insensitive).
- `reported_from`, `reported_to`: ISO 8601 date or datetime, inclusive. A
  date-only `reported_to` includes the whole UTC day.
- `recency`: `today`, `24h`, `7d`, or `30d`. It cannot be combined with an
  explicit reported range.
- `reservation_id`: exact internal reservation ID.
- `updated_since`: inclusive ISO 8601 watermark for incremental synchronization.
- `page`, `per_page`: pagination controls.
- `sort`: `reported_at` (default), `updated_at`, `priority`, `status`, or
  `issue_id`.
- `order`: `desc` (default) or `asc`.

For incremental synchronization, use
`updated_since=<watermark>&sort=updated_at&order=asc`. Because the watermark is
inclusive, consumers should de-duplicate by `issue_id` and advance the
watermark only after processing all pages.

Example:

```bash
curl --get 'https://your-host.example.com/api/v1/guest-issues' \
  --header 'X-API-Key: <guest_issues_read_key>' \
  --data-urlencode 'status=need_attention,in_progress,stuck' \
  --data-urlencode 'priority=Critical,High' \
  --data-urlencode 'updated_since=2026-08-29T12:30:00Z' \
  --data-urlencode 'sort=updated_at' \
  --data-urlencode 'order=asc' \
  --data-urlencode 'per_page=50'
```

Example response (abridged):

```json
{
  "data": [
    {
      "issue_id": 184,
      "listing": {"listing_id": 558675, "name": "Aurora Haven"},
      "portfolio": "Enchanted Havens",
      "category": "maintenance",
      "summary": "The hot tub did not reach the requested temperature.",
      "details": "The guest reported the issue after check-in.",
      "suggested_improvement": "Inspect the heater and document the result.",
      "reported_at": "2026-08-29T12:20:00Z",
      "source_date": "2026-08-29",
      "status": {
        "workflow": "open",
        "operational": "need_attention",
        "label": "Need attention"
      },
      "priority": "High",
      "severity": "material",
      "references": {
        "reservation_id": 123456,
        "conversation_ids": [78901],
        "review_id": null,
        "source_references": [
          {"source_type": "message", "source_id": 234567}
        ]
      },
      "linked_ticket_id": null,
      "assignee": null,
      "resolution": {
        "state": "feedback",
        "method": null,
        "details": null,
        "resolved_at": null,
        "resolved_by": null
      },
      "source": {"kind": "stay", "reference_count": 1},
      "created_at": "2026-08-29T12:20:00Z",
      "updated_at": "2026-08-29T12:25:00Z"
    }
  ],
  "pagination": {
    "page": 1,
    "per_page": 50,
    "total": 1,
    "pages": 1,
    "has_previous": false,
    "has_next": false,
    "previous_page": null,
    "next_page": null
  },
  "meta": {
    "sort": "updated_at",
    "order": "asc",
    "filters": {}
  }
}
```

### Get one Guest Issue

`GET /api/v1/guest-issues/{issue_id}`

Requires `guest_issues:read` or `*`. The resource uses the same data object as
the collection. An unknown ID returns:

```json
{"error": {"code": "not_found", "message": "Guest Issue not found."}}
```

## Health
- `GET /health`

## Listings, Insights, and Tags
- `GET /api/listings`
  - Query params: `tags`, `tag_logic` (`AND` or `OR`)
- `GET /api/insights/{listing_id}`
  - Query params: `refresh` (`true` or `false`)
- `GET /api/tags`
- `POST /api/tags`
  - Body: `{ "name": "pool", "color": "#00AAFF" }`
- `DELETE /api/tags/{tag_id}`
- `GET /api/tags/autocomplete`
  - Query params: `q`
- `GET /api/listings/{listing_id}/tags`
- `POST /api/listings/{listing_id}/tags`
  - Body: `{ "tags": ["pet-friendly", "dock"] }`
- `DELETE /api/listings/{listing_id}/tags/{tag_id}`

## Dashboard
- `GET /dashboard/api/data`
  - Query params: `ticket_limit`, `occupancy_months`

## STR Signal Brain
Brain is served by the separate `brain.yourcottoncandy.com` service, but uses the same Google session and API-key authentication patterns.
For local HTTP OAuth testing, set `BRAIN_ALLOW_INSECURE_OAUTH=True`; production should leave it `False` and rely on the Nginx `X-Forwarded-Proto` header.
PriceLabs read-only snapshots use `PRICELABS_API_KEY`, `PRICELABS_BASE_URL=https://api.pricelabs.co/v1`, and `PRICELABS_PMS_NAME=hostaway` by default.
For high-confidence revenue analysis, the PriceLabs connector reads `listing_prices` with a forward date window and `reason=true`, normalizes day-level booking indicators from price rows, and also reads `listing_metrics` for occupancy, revenue, ADR, RevPAR, and market comparisons when credentials allow it.

- `GET /api/brain/today`
- `GET /api/brain/portfolios`
- `GET /api/brain/portfolios/{portfolio_id}`
- `GET /api/brain/signals`
  - Query params: `portfolio_id`, `category`, `status`, `severity`, `audience`, `limit`
- `PATCH /api/brain/signals/{signal_id}/status`
  - Body: `{ "status": "acknowledged|watching|resolved|ignored|escalated" }`
- `GET /api/brain/booking-health`
- `GET /api/brain/open-loops`
- `GET /api/brain/data-foundation`
  - Query params: `portfolio_id`
  - Returns source health, active fact counts by source/type, derived metric counts, recent normalized facts, and recent metric snapshots.
- `GET /api/brain/data-foundation/audit`
  - Query params: `portfolio_id`
  - Returns readiness status, score, source gaps, source freshness, required fact/metric coverage, per-listing coverage, and PriceLabs pricing-to-booking-pattern match coverage for the analytical data foundation.
- `GET /api/brain/data-foundation/facts`
  - Query params: `portfolio_id`, `source_key`, `fact_type`, `status`, `listing_id`, `reservation_id`, `guest_id`, `occurred_from`, `occurred_to`, `limit`
  - Returns normalized provenance-backed facts for future Brain product surfaces.
- `GET /api/brain/data-foundation/metrics`
  - Query params: `portfolio_id`, `metric_name`, `category`, `grain`, `status`, `listing_id`, `metric_date`, `metric_from`, `metric_to`, `horizon_days`, `limit`
  - Returns decision-ready metric snapshots derived from the fact layer, such as booking occupancy, booking-health severity, PriceLabs 30-day pricing/min-stay indicators, message/review risk, and month-to-date finance totals.
  - Forward booking occupancy uses Hostaway reserved nights divided by reserved plus available nights; blocked inventory is excluded from the denominator and cannot be interpreted as weak demand.
  - `booked_nights_next_30d` counts unique occupied dates from confirmed stays only. `reservation_revenue_next_30d` prorates reservation revenue to nights inside the forward window.
  - `pricelabs_avg_available_price_30d` matches PriceLabs recommended rates to dates Hostaway marks available. Price decisions should use this metric rather than the all-date PriceLabs average.
- `GET /api/brain/intelligence`
  - Query params: `category`, `status`, `limit`
  - Returns durable Codex-authored cross-source intelligence rows. These are generated from local data packets and imported by Codex; this layer does not call the OpenAI API from the app.
- `POST /api/brain/ask`
  - Body: `{ "question": "What are the biggest risks today?" }`
- `POST /api/brain/runs/morning`
- `POST /api/brain/runs/afternoon`
- `POST /api/brain/runs/manual`
- `POST /api/brain/runs/aggregate`
  - Query params: `pull=true` to refresh due Hostaway sources, a bounded recent Hostaway message tail, PriceLabs, calendar, booking-health, and memory snapshots before materializing facts.
  - The normal message tail is controlled by `BRAIN_HOSTAWAY_MESSAGE_TAIL_HOURS` and `BRAIN_HOSTAWAY_MESSAGE_TAIL_MAX_RESERVATIONS`.
  - Add `deep=true` only for an intentional expensive Hostaway refresh, including full message backfill when the normal Hostaway sync would stay bounded.
- `GET /api/brain/settings/data`
- `POST /api/brain/settings/bootstrap`
- `POST /api/brain/settings/portfolios`
- `POST /api/brain/settings/portfolio-listings`
- `POST /api/brain/settings/portfolio-users`
- `DELETE /api/brain/settings/portfolio-users`

### Brain Webhooks
- `POST /webhooks/twilio/whatsapp`
  - Public Twilio WhatsApp inbound webhook. Validates `X-Twilio-Signature` when `BRAIN_TWILIO_VALIDATE_SIGNATURE=True`.
- `POST /webhooks/twilio/whatsapp/status`
  - Public Twilio delivery-status callback for outbound WhatsApp messages and brief delivery logs.

### Codex Intelligence Workflow
- `python3 -m brain.jobs intelligence-pack --window-days 30`
  - Writes a compact JSON/Markdown packet under `data/brain/intelligence/packets/` for weekly Codex reasoning.
  - Packet v2 exposes decision readiness, evidence dates/confidence, open-date prices, and same-portfolio plus same-inventory-profile peer benchmarks. Bundle listings are not compared with their component units, and bundle calendar holds without direct confirmed reservations are blocked from demand interpretation.
- `python3 -m brain.jobs intelligence-import --insights-file path/to/insights.json --run-key <packet-run-key>`
  - Imports Codex-authored insights into `codex_intelligence_runs` and `codex_intelligence_insights`.
- `python3 -m brain.jobs intelligence-list`
  - Lists stored intelligence rows for verification.

## Tickets
All ticket APIs are prefixed with `/tickets`.

- `GET /tickets/api/tickets`
  - Query params: `listing_id`, `listing_ids`, `assigned_user_id`, `status`, `priority`, `category`, `issue_title`, `tags`, `tag_logic`, `search`, `past_due`, `recurring`, `due_days`
- `GET /tickets/api/tickets/{ticket_id}`
- `POST /tickets/api/tickets`
- `PUT /tickets/api/tickets/{ticket_id}`
- `POST /tickets/api/tickets/{ticket_id}/transition` — atomically update the
  status and assignee and add its required handoff comment. Body:
  `{"status":"In Progress","from_status":"Open","assigned_user_id":12,"from_assigned_user_id":4,"comment_text":"@Alex Kim Starting work."}`.
- `DELETE /tickets/api/tickets/{ticket_id}`
- `GET /tickets/api/tickets/{ticket_id}/comments`
- `POST /tickets/api/tickets/{ticket_id}/comments`
- `DELETE /tickets/api/tickets/{ticket_id}/comments/{comment_id}`
- `POST /tickets/api/tickets/suggest`
- `GET /tickets/api/listings/{listing_id}/issues`
- `GET /tickets/api/users`
- `GET /tickets/api/tickets/{ticket_id}/tags`
- `POST /tickets/api/tickets/{ticket_id}/tags`
- `DELETE /tickets/api/tickets/{ticket_id}/tags/{tag_id}`

### Ticket Images
- `POST /tickets/api/tickets/{ticket_id}/images`
- `GET /tickets/api/tickets/{ticket_id}/images`
- `DELETE /tickets/api/tickets/{ticket_id}/images/{image_id}`
- `POST /tickets/api/comments/{comment_id}/images`
- `GET /tickets/api/comments/{comment_id}/images`
- `DELETE /tickets/api/comments/{comment_id}/images/{image_id}`
- `GET /tickets/api/images/{image_id}`
- `GET /tickets/api/images/{image_id}/thumbnail`
- `POST /tickets/api/recurring/process`

## Reviews
All review APIs are prefixed with `/reviews`.

- `GET /reviews/api/unresponded`
  - Query params: `tag_ids`
- `GET /reviews/api/filters`
- `POST /reviews/api/filters`
- `PUT /reviews/api/filters/{filter_id}`
- `DELETE /reviews/api/filters/{filter_id}`
- `GET /reviews/api/filters/{filter_id}/reviews`
  - Query params: `sort_by`, `sort_order`

## Knowledge Base
All knowledge APIs are prefixed with `/knowledge`.

- `POST /knowledge/api/documents`
- `GET /knowledge/api/documents`
  - Query params: `listing_id`, `listing_ids`, `tag_ids`, `search`, `page`, `per_page`
- `GET /knowledge/api/documents/{document_id}`
- `GET /knowledge/api/documents/{document_id}/file`
  - Query params: `download`
- `POST /knowledge/api/documents/search`
- `PUT /knowledge/api/documents/{document_id}`
- `DELETE /knowledge/api/documents/{document_id}`

## Sync
All sync APIs are prefixed with `/sync`.

- `GET /sync/api/history`
- `GET /sync/api/running-status`
- `GET /sync/api/job/{job_id}/detail`
- `GET /sync/api/{sync_run_id}/detail`
- `POST /sync/api/full`
- `POST /sync/api/incremental`
- `GET /sync/api/status/{job_id}`

## Users and Auth
These endpoints are available but are typically used by the web UI.

- `GET /auth/api/profile`
- `PUT /auth/api/profile`
- `GET /admin/api/users`
- `POST /admin/api/users/{user_id}/approve`
- `POST /admin/api/users/{user_id}/revoke`
- `POST /admin/api/users/{user_id}/role`
- `DELETE /admin/api/users/{user_id}`

## Activities
Admin activity APIs are prefixed with `/admin/api/activities`.

- `GET /admin/api/activities`
  - Query params: `start_date`, `end_date`, `user_id`, `activity_type`, `action`, `entity_type`, `entity_id`, `page`, `per_page`
- `GET /admin/api/activities/reports/ticket-metrics`
  - Query params: `start_date`, `end_date`, `group_by`

## Procurement

**Contract updated: 2026-10-15.** Procurement runs on the main Cotton Candy
host, with the UI at `/procurement/` and JSON endpoints under
`/procurement/api`. Production base URL: `https://yourcottoncandy.com`.
This section is published in both `/api-docs#procurement` and `/api-docs.md`.

This Phase 1 API captures requests, records human purchasing decisions and
retailer purchases, tracks shipments, verifies supplies at the property, and
reports costs. It does not execute checkout, move money, send WhatsApp messages,
import retailer emails, or poll carriers. Ethan supplies its own intake,
notification, and polling integration.

### Procurement authentication and write rules

- Use one authentication header: `X-API-Key` or `Authorization: Bearer`.
  Procurement currently requires the `Full API` (`*`) profile. A
  `guest_issues:read` key cannot call these endpoints. Full access still cannot
  approve purchases, correct final receipt costs, or edit the directory.
- Browser users need approved accounts and the Procurement feature. Admins have
  access; additional staff must be granted access in Team settings. Linked
  housekeeper accounts see only assigned properties and cannot purchase, read
  reports, download receipts, or see private shipping addresses.
- Every POST/PATCH requires an `Idempotency-Key` header, at most 180 characters.
  Persist a fresh UUID per logical action before sending. Retry transport errors
  with the same key, method, path, actor, and exact JSON bytes. A successful
  replay returns the original response; changed content using that key is 409.
  Multipart replay compares form fields, filenames, and file hashes instead of
  boundary bytes. Failed actions do not consume the key.
- Request edits/actions use the latest integer request `version`. Order edits/
  actions use the latest integer order `version`. Shipment edits/delivery use
  the **parent order version**, not a shipment version. Missing/stale versions
  return 409. Receipt uploads and admin directory saves are exceptions: they
  use idempotency but do not require a version. Uploading a receipt increments
  the order version.
- On a version conflict, fetch the current record and review it. If still valid,
  submit the updated operation with a new key. After an ambiguous timeout,
  retry the original key first; do not blindly create a second operation.
- Browser mutations additionally require `X-CSRF-Token` from the procurement
  page's session. API-key calls do not require a browser cookie or CSRF token.
- Successful JSON operations return HTTP 200 with the resource object directly,
  or the collection wrapper described below. These endpoints do not use the
  `/api/v1` success envelope, pagination, or Guest Issues rate-limit headers.
  Errors use `{"error":"Human-readable message"}`. Handle 400 validation, 401
  authentication, 403 permission, 404 missing record, 409 conflict, 413 oversized
  upload, and 500 server failure. Do not parse human error text as a stable code.
- Quantities are positive base units with at most three decimal places; send
  decimal strings. Monetary inputs (`unit_price`, `tax`, `shipping`, `discount`,
  `amount`, `actual_charged`) are nonnegative major-unit decimal strings with at
  most two decimal places. Returned `*_cents` fields are integer minor units.
  Supported currencies are USD, CAD, GBP, EUR, AUD, MYR; never combine currencies.
- Datetimes must include an offset or Z; responses normalize to UTC with Z.
  Purchase/refund dates use YYYY-MM-DD. Body limit is 1 MiB, with at most 100
  objects per item/line/allocation array. Receipt files have a separate 10 MiB
  limit and 11 MiB multipart request limit.

### Procurement discovery and reads

- `GET /procurement/api/bootstrap` returns `properties`, eligible purchasing
  `users`, `staff`, active/inactive `locations`, `catalog`, `statuses`,
  `priorities`, `currencies`, `can_approve`, `can_purchase`, `staff_id`, `user_id`.
  `account_users` is populated only for human admins. Use existing IDs, active
  records, and verified property assignments; never invent IDs or addresses.
- `GET /procurement/api/requests` returns `{"requests":[...]}`, newest first.
- `GET /procurement/api/requests/{id}` returns request detail, including
  `messages`, the latest 200 `activity` records, and `possible_duplicates`.
- `GET /procurement/api/orders` returns `{"orders":[...]}`, highest ID first.
- `GET /procurement/api/orders/{id}` returns order detail, activity, and
  `approved_snapshot`. Order reads require purchasing access.

Request objects include `id`, display `code` (PR-0001), `version`, `title`,
`listing_id`, property snapshot labels/tags/timezone, requester/owner IDs and
names, priority, deadlines, `status`, `status_label`, `flags`, and `items`.
Item objects include `id`, `catalog_id`, `name`, `category`, `unit`, `quantity`,
`requested`, `reserved`, `ordered`, `delivered`, `confirmed`, `remaining`,
`cancelled`, `cancellation_reason`, and `allocations`. Allocation objects expose
`id`, `order_id`, `line_id`, vendor/state, active `quantity`, `confirmed`,
`problem_quantity`, `reportable`, `confirmable`, `property_confirmable`, and
confirmation history. `confirmable` is bounded by recorded delivery;
`property_confirmable` is bounded by the outstanding purchased quantity.

Order objects include `id`, display `code` (PO-0001), `version`,
`purchase_revision`, `vendor`, `reference`, `currency`, destination snapshot,
`state`, expense `responsibility`, totals, purchase/reconciliation dates,
approval metadata, `lines`, `shipments`, and receipt `attachments` (IDs/names).
Lines include their IDs, base quantity, `pack_size`, `unit_price_cents`,
`landed_cents`, `unallocated`, `unshipped`, delivered quantity, allocations, and
refunds. Use returned IDs and versions for subsequent actions.

### Procurement intake and clarification

`POST /procurement/api/requests` creates one property/shared-storage request
with one or more items. Example IDs are illustrative; replace from bootstrap:

```json
{
  "title": "Toilet paper restock",
  "listing_id": 101,
  "requester_staff_id": 1,
  "owner_user_id": 7,
  "priority": "normal",
  "required_by": "2026-10-20T16:00:00-07:00",
  "items": [{"catalog_id": 3, "quantity": "24"}],
  "source": {
    "channel": "whatsapp:provider:group-id",
    "message_id": "provider-message-id",
    "body": "Original message preserved verbatim"
  }
}
```

`title` and 1–100 items are required. Each item uses `catalog_id` or a custom
`name` and `unit` (optional `category`, default supplies). A catalog item's
canonical name/unit must match; no automatic unit conversion occurs. Quantity
may be null/omitted when unknown. Use either `listing_id` or
`storage_location_id`, or omit both while clarifying. Use `requester_staff_id`
for an active assigned housekeeper, otherwise provide `requester_name`.
`owner_user_id` may be null; API intake does not automatically assign Ethan as
the purchasing owner. Capture incomplete requests immediately and clarify missing
information. Never infer a quantity, address, or deadline without evidence.

Priorities are `critical`, `high`, `normal`, `planned`. The default human review
targets are 30 minutes, 4 hours, 24 hours, 72 hours respectively. Optional
`response_due_at` overrides this target separately from `required_by`. These
are staff response targets, not an Ethan response SLA or an on-call guarantee.

A `source` includes `channel` (max 80), optional provider `message_id` (max 240),
required original `body` when using an ID (max 20,000), and optional HTTP(S)
`url`. The same source channel/message ID/destination replays an existing
request with `source_replayed:true`; it does not apply changed intake details.
One message can create separate requests for separate properties. Similar
requests appear under `possible_duplicates`; review before linking a follow-up.

`PATCH /procurement/api/requests/{id}` accepts current `version` plus any of
`title`, `requester_name`, `listing_id`, `requester_staff_id`, `owner_user_id`,
`priority`, `required_by`, `response_due_at`, `review:true`, `source`, `items`.
A source appends a traceable message; duplicate source IDs on the request are
ignored. Item rows with `id` update that item; rows without `id` append items.
Rewrites require the item's full catalog/custom fields and quantity and are
blocked after active reservation/purchasing. Property changes are blocked once
allocations exist. Linked housekeepers can append sources, not edit staff fields.
Do not use `review:true` for an automatic acknowledgment: this records review by
the assigned human purchasing owner.

```json
{
  "version": 2,
  "source": {
    "channel": "whatsapp:provider:group-id",
    "message_id": "follow-up-message-id",
    "body": "We need 24 rolls by Tuesday afternoon."
  }
}
```

Statuses are calculated: `new`, `in_progress`, `awaiting_confirmation`,
`completed`, `cancelled`. Needs information, unassigned ownership, approval,
late responses, and delivery problems are flags. There is no writable status or
manual completion endpoint. Always use the returned status.

### Procurement purchasing

`POST /procurement/api/orders` creates a draft. `PATCH /procurement/api/orders/{id}` fully replaces a draft/approved purchase using
current `version`, `change_reason`, and the complete order body. Every revision
clears approval and replaces line/allocation IDs; fetch fresh request/order
versions afterwards. Purchased orders cannot be rewritten.

```json
{
  "vendor": "Retailer name",
  "currency": "USD",
  "location_id": 2,
  "responsibility": "owner",
  "tax": "1.40",
  "shipping": "0.00",
  "discount": "0.00",
  "notes": "Verified destination and product",
  "lines": [{
    "catalog_id": 3,
    "name": "Retailer's actual 24-roll product",
    "quantity": "24",
    "pack_size": "24",
    "unit_price": "17.49",
    "substitution_reason": "Retailer SKU label for the requested product",
    "allocations": [{"item_id": 8, "quantity": "24"}]
  }]
}
```

`location_id` must identify an active verified destination. `responsibility`
is `owner`, `company`, `included`, or `unassigned`. `quantity` is always base
units; a 24-roll pack at $17.49 is quantity 24, pack_size 24, unit_price 17.49,
not 24 packs. `pack_size` defaults to 1. Custom purchase lines require name/unit.
Different product names/catalog products allocated to a request require
`substitution_reason`. Purchase and request units must always match, even for
substitutions.
One line may split between requests; unallocated quantities remain shared stock.
Reserved/ordered quantities cannot exceed outstanding need.

Order actions use `POST /procurement/api/orders/{id}/{action}`:

- `approve`: `{"version":3}`. Human admin browser session only. API keys receive 403.
  Every purchase requires approval of its current purchase revision and address.
- `place`: `{"version":4,"reference":"retailer-order-id","purchased_on":"2026-10-15"}`.
  Records a purchase already made externally. Requires current human approval;
  destination changes or inactive receiving staff block it. Never claim this
  endpoint performs checkout. Purchase date cannot be in the future.
- `cancel`: `{"version":3,"reason":"No longer needed"}`. Draft/approved only;
  releases reservations. Purchased orders need explicit problems/returns/refunds.
- `allocate`: `{"version":5,"line_id":4,"item_id":8,"quantity":"6"}`.
  Allocates available shared stock from a purchased order to a requested item.
- `refund`: `{"version":6,"line_id":4,"amount":"3.50","refunded_on":"2026-10-15","reason":"Actual refund received"}`.
  Records money already refunded, not a refund request. Cumulative refunds cannot
  exceed the line's landed cost; date must be from purchase through today.
- `adjust-costs`: `{"version":7,"reason":"Final receipt differs","tax":"1.50","lines":[{"line_id":4,"unit_price":"17.99","pack_size":"24"}]}`.
  Human admin only, purchased order with a receipt. Corrects prices/charges while
  preserving original approval history. Clears prior reconciliation.
- `reconcile`: `{"version":8,"actual_charged":"19.49"}`. Purchased order with
  attached receipt; actual charge must equal current order total exactly.

Order states are `draft`, `approved`, `ordered`, `cancelled`. `version` changes
on operational writes; `purchase_revision` changes on purchase edits. A receipt
upload changes version without invalidating the purchase approval. Reload both
request and order after quantity-changing actions; both versions may change.

### Procurement shipments

- `POST /procurement/api/orders/{id}/shipments` creates a partial/full shipment:
  `{"version":5,"carrier":"Carrier","tracking":"tracking-id","expected_at":"2026-10-18T18:00:00Z","lines":[{"line_id":4,"quantity":"12"}]}`.
  Requires a purchased order and at least one line; cumulative active shipment
  quantities cannot exceed purchased base units.
- `PATCH /procurement/api/shipments/{shipment_id}` accepts parent order `version`
  plus `carrier`, `tracking`, `expected_at`, or `cancelled_reason`. Cancellation
  releases unshipped capacity and retains the history. Delivered/cancelled
  shipments cannot be edited.
- `POST /procurement/api/shipments/{shipment_id}/deliver` accepts parent order
  `version` and optional timezone-aware `delivered_at` (defaults now, not future).
  It records the entire shipment delivered; split shipments for partial delivery.
  This never confirms usable supplies at a property or completes the request.

### Procurement property confirmation and exceptions

`POST /procurement/api/requests/{id}/confirm` records actual usable supplies at
the property, confirmed by an active housekeeper assigned to that property:

```json
{
  "version": 6,
  "allocation_id": 9,
  "quantity": "12",
  "staff_id": 1,
  "listing_id": 101,
  "note": "Housekeeper confirms 12 usable rolls are at the property",
  "evidence": {"reference": "whatsapp:provider:group-id:message-id"},
  "confirm_delivery": true
}
```

API/staff entries on behalf of a housekeeper require `evidence.reference`.
The evidence must refer to actual housekeeper confirmation. A linked
housekeeper login can confirm as itself without an external evidence reference.
`listing_id` must be the original property, or the real receiving property for
shared stock. Never use a carrier scan, retailer promise, or receipt at a
housekeeper's home as proof that supplies are at the rental property.

By default quantity is bounded by already recorded delivery. Set
`confirm_delivery:true` only when the source explicitly confirms physical
receipt despite missing/late carrier tracking; it remains bounded by purchased
outstanding quantity. Partial confirmation leaves the remaining need open.
Completion follows only when all noncancelled item quantities are confirmed.

Additional `POST /procurement/api/requests/{id}/{action}` operations:

- `return`: same fields as confirm, with positive quantity and note/evidence.
  Removes previously confirmed usable goods from that property's quantity and
  reopens the need. The quantity cannot exceed previous confirmations there.
- `problem`: `{"version":7,"allocation_id":9,"quantity":"2","kind":"damaged","reason":"Two units unusable"}`.
  Kinds: `lost`, `damaged`, `incorrect`, `retailer_cancelled`. Releases outstanding
  quantity for replacements; already confirmed goods require a return instead.
- `cancel-item`: `{"version":8,"item_id":8,"quantity":"2","reason":"Not needed"}`.
  Purchasing staff only; only unpurchased/unreserved quantities may be cancelled.
- `cancel`: `{"version":9,"reason":"Request withdrawn"}`. Purchasing staff
  only; entire request must be unpurchased and unreserved.

Returns/problems do not automatically refund money. Refunds do not establish
physical return, replacement, or receipt. Operational completion and financial
reconciliation remain separate.

### Procurement receipts, reports, and directory

- `POST /procurement/api/orders/{id}/receipts`: multipart `file`, with
  Idempotency-Key. Accepts PDF/PNG/JPEG by file signature, up to 10 MiB. Returns
  the updated order. No version field required; re-read before subsequent writes.
- `GET /procurement/api/receipts/{attachment_id}`: authenticated attachment
  download. Requires purchasing access; private/no-store, not a public URL.
- `GET /procurement/api/report?month=YYYY-MM`: returns `month` and `currencies`.
  Each currency group has per-property `allocated_cents`, `confirmed_cents`,
  currency totals, and `unallocated_cents` for shared stock. Purchase costs use
  purchased_on; refunds use refunded_on, including negative later-month amounts.
  Confirmed cost uses property confirmations through month-end. Shipping, tax,
  discounts, and rounding conserve the total. No currency conversion is applied.
- `GET /procurement/api/report.csv?month=YYYY-MM`: authoritative UTF-8 CSV with
  the same figures in major units; authenticated private download.
- `POST /procurement/api/directory/{kind}`: human admins only; kinds `staff`,
  `locations`, `catalog`. Optional `id` edits an existing record, otherwise
  creates. Saves require Idempotency-Key but no version. `active` is boolean.
  Staff: name, phone, optional user_id, listing_ids. Locations: label, kind
  (housekeeper/property/storage), address, instructions, staff_id or listing_id.
  Catalog: name, canonical unit, category, pack_description. Deactivate old
  records to retain history. Linked logins still need Procurement feature access.

### Procurement Ethan event and reminder contract

- `GET /procurement/api/events?after=0` returns up to 200 request-linked events
  in ascending event_id order: `{"events":[...],"next_cursor":123}`.
  Cursor must be a nonnegative integer; gaps are normal. Empty results preserve
  the supplied cursor. This is a polling feed, not a webhook or live listener.
- Events include event_id, action, ticket_id, request_code, property, current
  status, owner_user_id, requester_phone (active requester only), and
  source_messages (channel/message_id references). Destination addresses and
  original message bodies are not in the feed. Fetch request detail for context.
- `status` reflects **current state at polling time**, not the historical state
  when the event happened. Coalesce multiple events for one request and fetch
  latest details before sending an update. Pure order/receipt/refund/directory
  events without a request link do not appear; use order reads for finance.
- Persist next_cursor only after downstream processing succeeds. Deduplicate
  outbound actions by event_id and recipient. Drain full batches before waiting.
  A 30–60 second initial poll interval with backoff on errors is reasonable;
  this is guidance, not a server-side schedule.
- `GET /procurement/api/attention` returns `{"requests":[...]}` for active
  requests with flags or critical priority. Escalate missing human owners,
  unanswered clarification, overdue staff reviews, and delivery risk to staff.
  Compare with durable previous state; do not repeat unchanged reminders on each
  poll. Suppress feedback loops from Ethan's own intake messages.
- Learn the workflow and discover actual directory readiness before operating.
  If staff/property mapping, verified addresses, human owner, or approval is
  missing, ask the responsible human. Do not invent setup or bypass approval.
  Learning this document alone does not install a worker or enable notifications.
