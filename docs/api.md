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
