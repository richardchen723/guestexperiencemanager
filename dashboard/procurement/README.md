# Cotton Candy procurement

The procurement workspace is at `/procurement/` in the existing Flask app. It
uses existing Cotton Candy property IDs, tags, accounts, and feature access.
All new operational data lives in the separate `procurement` PostgreSQL schema.
Existing operational tickets and bookkeeping records are not rewritten.

## Operating workflow

1. Capture one request for a property group or explicitly selected properties and add
   the supplies needed. **Quantities (optional)** is collapsed by default; leave
   quantities blank for purchasing staff to decide. Blank quantities never add
   **Needs information**. Choose **Assign properties later**, **Entire group**, or
   specific properties within a group. Each group supply is entered once; an
   optional quantity is a group total and is never multiplied across properties.
   Membership is frozen at capture. A group satisfies intake's property context;
   missing deadlines still appear under **Needs information**.
   Shared-storage requests are also supported; final receipt must still identify
   the real property receiving the supplies.
2. Assign a named staff owner from all approved user accounts and review the
   request. Accounts without Procurement access and linked housekeepers are
   available; assigning ownership does not change their Team access or purchasing
   permissions. Pending/removed accounts and the API service account are excluded.
   Default human
   review targets are 30 minutes / 4 hours / 24 hours / 72 hours for critical /
   high / normal / planned requests. These are elapsed-time defaults, not a
   promise of business-hour or on-call coverage. Staff can change the response
   deadline independently of the required-by deadline.
3. Open **Purchases → To buy**. Group supplies show **Assign properties**: choose
   actual properties and enter their quantities. A partial distribution leaves the
   remainder open. For an unknown total, the submitted quantities cover the need;
   optionally enter a total to leave a remainder for later. Property allocation
   never records a purchase or delivery. Assigned supplies and their history are
   preserved when editing the remaining group request. Group scope changes later
   do not alter this request. Supplies without a quantity appear with **Set
   quantity**: staff choose the total to supply per property, then select how
   much to buy now. A target of 12 and purchase of 8 leaves 4 outstanding. Setting
   the target uses the versioned request PATCH with an audit record; housekeepers
   cannot change it. Select outstanding needs across requests and adjust how
   much to buy now. Addresses default only when the roster resolves
   to exactly one verified destination. Choose ambiguous/missing addresses
   explicitly, or use **Deliver selected to** for deliberate central receiving.
   Matching products/units consolidate per destination.
   Draft one or more purchases. A retailer order can cover multiple requests,
   and each requested item can be fulfilled through multiple retailer orders.
   Unallocated purchased quantity remains visible as shared stock at its receiving
   location. Whole packs round separately per destination. Each purchase records
   one actual retailer order; use separate purchases for separate order numbers.
4. An administrator reviews products, quantities, substitutions, price, expense
   responsibility, allocations, and an address snapshot, then approves the
   current purchase revision. Initial policy is approval for **every purchase**;
   automatic purchasing and configurable spending tiers are not enabled.
5. A purchaser places the order with the retailer and records its reference and
   purchase date. This application does not execute checkout or move money.
6. Record split shipments and delivered quantities. Tracking and ETA can be
   corrected; undelivered shipment records can be cancelled with a reason.
7. The assigned housekeeper confirms usable supplies physically at the property.
   **Confirm property receipts** supports explicitly selected supplies at several
   assigned properties in one submission, including partial quantities. An invalid
   row rolls back all rows. Property progress is independent; shared requests show
   how many properties are ready. Housekeepers see only their assigned portions,
   without sibling contacts, shared messages or finance.
   Staff or Ethan recording confirmation on someone's behalf must provide a
   source-message/evidence reference. A housekeeper using their own linked
   account can confirm directly. Physical receipt can be explicitly recorded
   even when carrier tracking is absent or late.
8. Completion is derived from item quantities. Carrier delivery alone never
   completes a request. Unallocated group supplies also keep it open. Returns reopen the need; lost, damaged, incorrect, or
   retailer-cancelled quantities release replacement purchasing capacity.
9. Attach the final receipt and reconcile the charged amount. Reconciliation is
   independent of operational completion. Final receipt cost corrections need
   an administrator, receipt, and recorded reason; original approval snapshots
   remain in history. Actual refunds are separate dated records.

The five request statuses are **New**, **In Progress**, **Awaiting Property
Confirmation**, **Completed**, and **Cancelled**. Needs information, approval,
unassigned ownership, allocation pending, overdue responses, delivery risk, and fulfillment problems
are flags. Staff cannot arbitrarily set a request to Completed.

## Housekeepers and property assignments

Open **Procurement → Housekeepers**, or `/procurement/#housekeepers`. Add/edit
names, optional WhatsApp numbers, active status, and property assignments. Search
by person/property, filter the roster by property, and filter the assignment
picker by property group. Select/Clear shown only affects visible matches;
assignments outside the current filter are preserved. Contact details can be
added later. Use **Add address / Edit address** for the housekeeper's verified
receiving address. Other destinations and supplies are under **Delivery & catalog**.

Admins maintain the roster; purchasing staff can view it. Linking an existing
account is optional and does not grant login/feature access. Deactivation keeps
history while removing the person from new requests/receiving choices. Existing
orders retain their address snapshots and changed addresses require reapproval.
Staff edits use bootstrap's `version` to reject lost updates from concurrent
editors. Human account permissions remain in Team access.

For an explicitly supplied initial roster, use the generic importer with a
private JSON list of `{name, properties: [exact names], property_tags: [tags]}`.
`listing_ids`, `phone`, and `active` are optional. It resolves real properties,
rejects unknown/ambiguous names, previews by default, skips existing people,
and applies each import key once. It never overwrites later manual edits.
Keep roster/contact files outside the source repository.

```sh
python -m dashboard.procurement.import_roster --file /private/roster.json --key initial-roster
python -m dashboard.procurement.import_roster --file /private/roster.json --key initial-roster --apply
```

## Directory and access

Administrators already have access. Grant **Procurement** through the existing
Team access screen to additional purchasing staff; it is off by default for
ordinary accounts.

- Add housekeepers, property assignments, and optional linked login accounts.
  A linked ordinary account becomes a housekeeper account in this module: it
  sees assigned properties and request portions, and can add messages, report problems,
  and confirm/return supplies as itself. It cannot purchase, access financial
  reports, read private delivery addresses, download receipts, or approve orders.
  Group intake is restricted to its assigned properties; entire-group coverage is
  allowed only when every group member is assigned. Pending group supplies are
  visible only to their original housekeeper while still assigned in that scope.
  After staff allocate, each housekeeper sees only their own property portions.
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
per explicit property set/storage destination. One message concerning two
properties can create one request with `listing_ids:[101,102]` and a `listing_id`
on each item. Each selected property needs a positive/unknown item; omit quantities
that are not needed. Legacy single-property payloads remain valid.
Similar messages appear as possible duplicates; they are not silently merged.
For group intake use `group_tag` from bootstrap's `property_groups`, with
`group_coverage:"assign_later"` or `"entire_group"`, and no property IDs required.
The request returns its frozen `group` scope and `allocation_pending` flag. Source
replay also includes the original group/coverage and survives later allocation.

Use `PATCH /procurement/api/requests/<id>` with the current `version` to attach
a follow-up under `source`, clarify details, or add items. The response includes
current versions, item progress, allocations, and possible duplicates.

Other API groups:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/requests`, `GET /api/requests/<id>` | Requests and complete history |
| `POST /api/requests/<id>/allocate-properties` | Split an unassigned group supply across properties, preserving a partial remainder |
| `POST/PATCH /api/orders[/<id>]` | Draft or revise purchasing details |
| `POST /api/orders/<id>/approve` | Human administrator approval; API keys cannot approve |
| `POST /api/orders/<id>/place` | Record an externally placed retailer purchase |
| `POST /api/orders/<id>/allocate` | Assign unallocated purchased stock |
| `POST /api/orders/<id>/shipments` | Add partial or complete shipments |
| `PATCH /api/shipments/<id>` | Correct tracking/ETA or cancel an undelivered shipment |
| `POST /api/shipments/<id>/deliver` | Record delivery; does not close a request |
| `POST /api/requests/<id>/confirm` | Record housekeeper confirmation at the property |
| `POST /api/requests/<id>/confirm-batch` | Atomically record selected property/item receipts |
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
Receipt uploads and location/catalog saves require idempotency but no version.
Housekeeper edits require the current staff version returned by bootstrap.
Stale writes return 409 instead of overwriting another person's work.

For property confirmation, include `allocation_id`, `quantity`, `staff_id`,
`listing_id`, `note`, and `evidence.reference` when acting on behalf of a
housekeeper. A shared request allocation can only be confirmed at its item's
property. Set `confirm_delivery: true` only when the source confirms actual
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
require deleting procurement history. The multi-property extension adds request
property snapshots, item property IDs, and per-line receiving snapshots. Migration
backfills legacy item property IDs without replacing item/allocation/confirmation
IDs. Existing requests and single-address orders retain compatible API shapes.
The group extension adds a JSON `group_snapshot` column, defaulting to `{}` for
existing requests. It does not rewrite existing item IDs or purchasing history.
Restart all workers together when enabling this version; older binaries do not
understand newly created group requests.

Run business and access regressions:

```sh
.venv/bin/python -m pytest dashboard/procurement/tests/test_workflow.py dashboard/procurement/tests/test_multi_property.py dashboard/procurement/tests/test_groups.py dashboard/auth/tests/test_features.py -q
node --test dashboard/procurement/tests/test_planning.cjs
```

For full real-app/PostgreSQL integration tests, create a disposable local DB:

```sh
createdb codex_procurement_qa_integration
DATABASE_URL=postgresql:///codex_procurement_qa_integration \
PROCUREMENT_QA_DATABASE_URL=postgresql:///codex_procurement_qa_integration \
  .venv/bin/python -m pytest dashboard/procurement/tests/test_api.py dashboard/procurement/tests/test_multi_api.py dashboard/procurement/tests/test_group_api.py -q
```

The integration suite refuses a nonlocal DB or a name outside the
`codex_procurement_qa_` prefix. It resets procurement data **only in that QA DB**.
It covers authentication, feature access, CSRF, housekeeper isolation, API-key
restrictions, replay protection, six simultaneous retries, competing buyers,
atomic rollback, private receipts, event cursors, and the complete workflow.
Shared-request regressions cover partial purchases across requests/retailers,
independent property receipts, batch confirmation rollback, sibling access
isolation, multiple addresses, cent conservation, competing buyers, and repeatable
upgrade from the deployed schema while preserving history.
Dependency-free JavaScript tests exercise the same grid editing, partial
purchase grouping and pack rounding helpers loaded by the browser, including
preserving the distinction between unknown and not-needed property quantities.
Group regressions cover frozen membership, scoped housekeeper intake, source
replay after allocation/tag removal, exact decimal totals, partial distributions,
concurrent allocation conflicts, immutable purchasing destinations, and additive
migration of existing requests.

The optional `dashboard.procurement.preview` entrypoint runs an isolated local
sample-data preview at `127.0.0.1:5056`, with purchasing/housekeeper view switches.
It refuses production or remote databases and is never loaded by the production
app. Its QA-only login hook is contained in that entrypoint.

Detailed inventory counts, predictive replenishment, automated checkout,
financial transfers/reimbursement payments, and automatic bookkeeping posting
remain outside Phase 1.
