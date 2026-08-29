# Hostaway Messages API

This document describes the HTTP API surface for third-party integrations. All endpoints are available for read and write access when authenticated with an API key.

**Base URL**
Use the same host and port as the dashboard application (for example `https://your-host.example.com`).

**Authentication**
Send an API key with every request.

Headers:
- `X-API-Key: <your_api_key>`
- `Authorization: Bearer <your_api_key>`

**Error Format**
Errors are returned as JSON:
```json
{"error": "Human-readable message"}
```

**Notes**
- File upload endpoints require `multipart/form-data`.
- Some endpoints return `401` if authentication is missing and `403` if access is denied.

## API Key Management (Admin)

- `GET /admin/api/api-keys`
  - List API keys (metadata only).
- `POST /admin/api/api-keys`
  - Create a new API key.
  - Body: `{"name": "Third Party Partner"}`
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
