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

Example:
```bash
curl -X POST \\
  -H "X-API-Key: <admin_api_key>" \\
  -H "Content-Type: application/json" \\
  -d '{"name":"Third Party Partner"}' \\
  https://your-host.example.com/admin/api/api-keys
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
