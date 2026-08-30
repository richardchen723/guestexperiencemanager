# API design review

## Scope

This review covers the dashboard, Tickets, Reviews, Knowledge, Sync, Activities,
KPI, Guest Issues, and STR Signal Brain HTTP surfaces as of ticket 698.

## Findings

1. **Authentication was all-or-nothing.** Every valid API key was accepted by
   the shared API decorators, including administrative and write routes. API
   keys now carry explicit scopes. Existing credentials retain `*` access for
   compatibility; new Guest Issues agents can be limited to
   `guest_issues:read`, and restricted keys are rejected from legacy APIs.
2. **The external contract was mixed with browser APIs.** Most current routes
   live under feature-specific `/.../api/...` paths and return UI-oriented
   payloads. Guest Issues starts a distinct `/api/v1/...` surface with explicit
   stability, authentication, error, pagination, sorting, and privacy rules.
3. **Response shapes and errors are inconsistent.** Existing APIs variously
   return arrays, objects, and string errors. They remain unchanged to avoid
   breaking the dashboard. New versioned APIs use `data`, `pagination`, `meta`,
   and machine-readable error codes.
4. **List limits are inconsistent.** Some endpoints use fixed limits, some are
   unbounded, and some paginate differently. The versioned convention is
   one-based pages, a default of 50, a maximum of 100, total counts, and an
   immutable-ID sort tiebreaker.
5. **Operational APIs lack one shared rate/audit boundary.** Guest Issues now
   reserves a per-key request slot and writes a minimal audit event before
   reading business data. Query values and guest text are excluded from this
   ledger. This boundary can be reused as other resources move to `/api/v1`.
6. **UI read models may expose more context than agents need.** The Guest Issues
   external serializer is separate from its browser serializer. It whitelists
   source-reference fields, maps messages to internal conversation IDs without
   returning content, omits guest identity, and redacts PII and credential-like
   strings from operator/analysis text.

## Compatibility strategy

- Do not silently change legacy response bodies or pagination.
- Keep current keys on `*` until their consumers are inventoried and moved to
  narrower scopes.
- Build all new third-party contracts under `/api/v1` with shared security and
  response conventions.
- Migrate one resource at a time, publish a replacement contract, observe audit
  usage, and only then schedule legacy deprecation.

## Recommended next API work

1. Add resource scopes such as `tickets:read`, `tickets:write`,
   `reviews:read`, and `brain:read`, then expose them in owner key management.
2. Add a request/correlation ID to versioned responses and audit records.
3. Publish an OpenAPI document generated from the versioned contracts and
   validate responses against it in CI.
4. Add cursor pagination for high-churn event feeds; retain page pagination for
   bounded operator collections.
5. Define retention and review procedures for `api_access_logs`, including
   alerts for repeated 401, 403, and 429 outcomes.
