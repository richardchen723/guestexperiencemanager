# Reservation financial synchronization

Some Hostaway reservations omit the generic tax amount even when canonical finance components contain tax. The importer now hydrates exact reservation IDs when list resources are incomplete, and fills missing tax only when active included components reconcile to the generic total and cleaning amount.

Absent, explicit null, invalid and valid zero values remain distinct. Sparse payloads preserve stored finance fields; component fallback preserves known positive and zero tax and reports conflicts. Financial source versions are checked independently from activity timestamps. A deliberate finance refresh can run even when ordinary source timestamps are unchanged. Exact-ID hydration is bounded to 100 reservations per normal sync run; failures preserve unknown tax.

The bounded repair command accepts a pinned audit scope and verified source evidence, produces a dry-run plan, and changes tax_amount only. It checks original values, locks rows, records actual owned changes atomically in sync_logs, and retains a private rollback ledger. Repeat application is idempotent. Rollback touches only this operation's owned changes and refuses concurrent drift. No API credentials, source financial exports, guest data, or production rollback files are committed here.

Tests use newly constructed synthetic reservations. PostgreSQL variants exercise transaction ownership, lock contention, concurrent repairs, idempotence and rollback. Set TAX_REPAIR_TEST_DATABASE_URL to an isolated hostaway_tax_repair_test database on a private Unix socket under /tmp/hostaway-tax-investigation- to run those variants. The synthetic CLI test mocks only the scope-reader boundary; production scope validation is unchanged.

The public runtime files are byte-identical to independently reviewed source commit 05347f0227c233f307fc448bb97479ba0314507a. The production release separately preserves its existing archive-filter customization. The deployment runner and financial repair evidence remain private operational artifacts.
