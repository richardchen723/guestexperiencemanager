# Cotton Candy procurement — Phase 1

The procurement workspace is at `/procurement/` in the existing Flask app. It
uses existing Cotton Candy property IDs, tags, accounts, and feature access.
All new operational data lives in the separate `procurement` PostgreSQL schema.
Existing operational tickets and bookkeeping records are not rewritten.

## Operating workflow

1. Capture one request per property, with multiple items. Incomplete property,
   quantity, or deadline information stays visible under **Needs information**.
   Shared-storage requests are also supported; final receipt must still identify
   the real property receiving the supplies.
2. Assign a named purchasing staff owner and review the request. Default human
   review targets are 30 minutes / 4 hours / 24 hours / 72 hours for critical /
   high / normal / planned requests. These are elapsed-time defaults, not a
   promise of business-hour or on-call coverage. Staff can change the response
   deadline independently of the required-by deadline.
3. Draft one or more purchases. A retailer order can cover multiple requests,
   and each requested item can be fulfilled through multiple retailer orders.
   Unallocated purchased quantity remains visible as shared stock.
4. An administrator reviews products, quantities, substitutions, price, expense
   responsibility, allocations, and an address snapshot, then approves the
   current purchase revision. Initial policy is approval for **every purchase**;
   automatic purchasing and configurable spending tiers are not enabled.
5. A purchaser places the order with the retailer and records its reference and
   purchase date. This application does not execute checkout or move money.
6. Record split shipments and delivered quantities. Tracking and ETA can be
   corrected; undelivered shipment records can be cancelled with a reason.
7. The assigned housekeeper confirms usable supplies physically at the property.
   Staff or Ethan recording confirmation on someone's behalf must provide a
   source-message/evidence reference. A housekeeper using their own linked
   account can confirm directly. Physical receipt can be explicitly recorded
   even when carrier tracking is absent or late.
8. Completion is derived from item quantities. Carrier delivery alone never
   completes a request. Returns reopen the need; lost, damaged, incorrect, or
   retailer-cancelled quantities release replacement purchasing capacity.
9. Attach the final receipt and reconcile the charged amount. Reconciliation is
   independent of operational completion. Final receipt cost corrections need
   an administrator, receipt, and recorded reason; original approval snapshots
   remain in history. Actual refunds are separate dated records.

The five request statuses are **New**, **In Progress**, **Awaiting Property
Confirmation**, **Completed**, and **Cancelled**. Needs information, approval,
unassigned ownership, overdue responses, delivery risk, and fulfillment problems
are flags. Staff cannot arbitrarily set a request to Completed.

## Directory and access

Administrators already have access. Grant **Procurement** through the existing
Team access screen to additional purchasing staff; it is off by default for
ordinary accounts.

- Add housekeepers, property assignments, and optional linked login accounts.
  A linked ordinary account becomes a housekeeper account in this module: it
  sees assigned properties and requests, and can add messages, report problems,
  and confirm/return supplies as itself. It cannot purchase, access financial
  reports, read private delivery addresses, download receipts, or approve orders.
- An approved account with Procurement access and no linked housekeeper profile
  is purchasing staff. Only administrators can approve purchases, change final
  receipt costs, or manage the directory.
- Add verified housekeeper, property, or regional-storage destinations. Deactivate
  former staff and addresses rather than deleting historical records. A changed
  address after approval blocks recording the purchase until it is revised and
  approved again.
- Standardize catalog units. For a request in **rolls**, buying a 24-roll pack is
  quantity 24, units per pack 24, and the actual pack price (for example $17.49).
  Pack pricing avoids rounding a fractional-cent per-roll price. Units never convert silently. Custom
  supplies are supported. Product substitutions need an explicit reason.

## Cost reporting

Amounts use integer minor currency units and quantities use three decimal
places. Supported currencies are USD, CAD, GBP, EUR, AUD, and MYR, reported
separately with no implicit conversion.

Tax, shipping, and discounts are allocated proportionally to product value
(quantity is the fallback for zero-price products). Largest-remainder rounding
preserves the exact order total. Costs split between property allocations and
unallocated shared stock without double counting.

Reports select purchases by their recorded purchase date and refunds by the
date received. A refund in a later month appears as a reduction in that later
month. **Allocated cost** reflects the property's assigned share of purchasing;
**Confirmed at property** shows the portion verified there through month-end.
This is an operational supply-cost report, not a consumption ledger or an
automatic owner statement. CSV export escapes spreadsheet formula inputs.

Receipts are private, authenticated PDF/PNG/JPEG downloads, capped at 10 MB.
Receipt bytes are deferred: purchasing lists and reports do not load the files.

## Ethan integration contract

The complete HTTP contract and examples are maintained in
[docs/api.md](../../docs/api.md#procurement), published at
`https://yourcottoncandy.com/api-docs#procurement` and `/api-docs.md`.
Procurement requires the Full API (`*`) profile; Guest Issues read-only keys
cannot call it. API keys cannot approve, correct final receipt costs, or edit
the directory.

The API uses the app's existing `X-API-Key` / Bearer authentication. Every write
requires a unique `Idempotency-Key`. Retrying the same action with the same key
returns its original result; reusing a key for different content is rejected.
Browser writes additionally require the session's CSRF token.

Create a request with `POST /procurement/api/requests`:

```json
{
  "title": "Guest supply restock",
  "listing_id": 101,
  "requester_staff_id": 1,
  "owner_user_id": 7,
  "priority": "normal",
  "required_by": "2026-10-20T16:00:00-07:00",
  "items": [{"catalog_id": 3, "quantity": "24"}],
  "source": {
    "channel": "whatsapp:provider:group-id",
    "message_id": "provider-message-id",
    "body": "Original message, preserved verbatim"
  }
}
```

Use actual IDs from `GET /procurement/api/bootstrap`. Leave unknown quantities
or destinations unset; never invent them. Source-message replay is deduplicated
per destination. One message concerning two properties can create two requests.
Similar messages appear as possible duplicates; they are not silently merged.

Use `PATCH /procurement/api/requests/<id>` with the current `version` to attach
a follow-up under `source`, clarify details, or add items. The response includes
current versions, item progress, allocations, and possible duplicates.

Other API groups:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/requests`, `GET /api/requests/<id>` | Requests and complete history |
| `POST/PATCH /api/orders[/<id>]` | Draft or revise purchasing details |
| `POST /api/orders/<id>/approve` | Human administrator approval; API keys cannot approve |
| `POST /api/orders/<id>/place` | Record an externally placed retailer purchase |
| `POST /api/orders/<id>/allocate` | Assign unallocated purchased stock |
| `POST /api/orders/<id>/shipments` | Add partial or complete shipments |
| `PATCH /api/shipments/<id>` | Correct tracking/ETA or cancel an undelivered shipment |
| `POST /api/shipments/<id>/deliver` | Record delivery; does not close a request |
| `POST /api/requests/<id>/confirm` | Record housekeeper confirmation at the property |
| `POST /api/requests/<id>/return` | Record supplies returned from the property |
| `POST /api/requests/<id>/problem` | Release lost, damaged, incorrect, or cancelled quantities |
| `POST /api/requests/<id>/cancel-item` | Cancel only unpurchased, unreserved quantities |
| `POST /api/requests/<id>/cancel` | Cancel an entirely unpurchased request |
| `POST /api/orders/<id>/receipts` | Multipart receipt upload (`file`) |
| `POST /api/orders/<id>/refund` | Record an actually received refund |
| `POST /api/orders/<id>/adjust-costs` | Administrator-approved final receipt correction |
| `POST /api/orders/<id>/reconcile` | Match actual charge to receipt/order total |
| `GET /api/report?month=YYYY-MM` | Property cost report by currency |
| `GET /api/report.csv?month=YYYY-MM` | Authoritative CSV download with the same totals |
| `GET /api/events?after=<event_id>` | Cursor-based procurement events for Ethan |
| `GET /api/attention` | Current staff follow-up and escalation queue |

All paths in the table have the `/procurement` prefix. Request and order writes
require the current `version` (shipment writes use the parent order's version).
Receipt uploads and directory saves require idempotency but no version.
Stale writes return 409 instead of overwriting another person's work.

For property confirmation, include `allocation_id`, `quantity`, `staff_id`,
`listing_id`, `note`, and `evidence.reference` when acting on behalf of a
housekeeper. Set `confirm_delivery: true` only when the source confirms actual
physical receipt despite absent/late tracking. Without it, confirmation is
limited to already recorded delivery quantities. Purchased quantities and
housekeeper assignments are enforced in both cases.

The event feed contains source-message references and the active requester's
phone, without shipping addresses. Persist `next_cursor` only after downstream
processing succeeds, deduplicate outbound messages by event ID, and collapse
superseded updates. Ticket status in an event is the **current** state at poll
time. Poll `/api/attention` for staff reminders; do not repeatedly notify about
unchanged conditions.

**Deployment boundary:** the intake, update, confirmation, event, and attention
APIs are implemented. The separately running Ethan worker still needs its
tool configuration pointed at these endpoints. This change does not install a
live WhatsApp listener, send external messages, import retailer email, or poll
carriers. Shipment updates can be entered manually or by a connected worker.

## Installation and verification

Normal application startup calls `init_procurement_database()` under the
existing database-initialization lock. It creates the schema, tables, additive
columns, and indexes idempotently. PostgreSQL writes use a transaction-scoped
procurement advisory lock to protect quantity aggregates and retry keys across
workers. The schema is independent, so rolling back application code does not
require deleting procurement history.

Run business and access regressions:

```sh
.venv/bin/python -m pytest dashboard/procurement/tests/test_workflow.py dashboard/auth/tests/test_features.py -q
```

For full real-app/PostgreSQL integration tests, create a disposable local DB:

```sh
createdb codex_procurement_qa_integration
DATABASE_URL=postgresql:///codex_procurement_qa_integration \
PROCUREMENT_QA_DATABASE_URL=postgresql:///codex_procurement_qa_integration \
  .venv/bin/python -m pytest dashboard/procurement/tests/test_api.py -q
```

The integration suite refuses a nonlocal DB or a name outside the
`codex_procurement_qa_` prefix. It resets procurement data **only in that QA DB**.
It covers authentication, feature access, CSRF, housekeeper isolation, API-key
restrictions, replay protection, six simultaneous retries, competing buyers,
atomic rollback, private receipts, event cursors, and the complete workflow.

The optional `dashboard.procurement.preview` entrypoint runs an isolated local
sample-data preview at `127.0.0.1:5056`, with purchasing/housekeeper view switches.
It refuses production or remote databases and is never loaded by the production
app. Its QA-only login hook is contained in that entrypoint.

Detailed inventory counts, predictive replenishment, automated checkout,
financial transfers/reimbursement payments, and automatic bookkeeping posting
remain outside Phase 1.
