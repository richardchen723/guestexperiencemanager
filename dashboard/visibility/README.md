# Enchanted Havens weekly search and AI visibility

Cotton Candy page: `/visibility/`. Each weekly summary links to
`/visibility/reports/{id}` with the full evidence, source links, search/business
metrics, provider coverage, action plan and publication history. The first real
record combines the October 27–28 baseline under the week of October 26, 2026;
it does not pretend that Gemini was rerun on October 28.

## Access and storage

Only existing approved, non-deleted administrators/owners with Listing Health
access can read pages or APIs. API writes/read reuse an existing full-access key
and its approved owner; restricted keys cannot access this feature. Browser POSTs
require `visibility_csrf` from the authenticated page session in X-CSRF-Token.
Responses are private/no-store and noindex. Model answers are rendered as escaped
text; evidence URLs allow only HTTPS without embedded credentials or signed tokens.
No arbitrary provider response is interpreted as instructions or executable HTML.

The explicit additive migration creates `visibility.weekly_reports` and its
indexes. It does not run at startup, alter existing tables, grant permissions, or
seed examples. Site scope is fixed to `enchantedhavens.com` in every read/write.
Summary queries do not load full captured answers; detail loads those on demand.

Records are append-only through supported APIs. One week has numbered revisions;
the list shows only the latest revision and detail preserves every earlier one.
Expected-revision checks, a PostgreSQL transaction lock, unique constraints and
idempotency keys prevent duplicates and stale overwrites. Retry an uncertain
publish with the identical key, actor and full content. Never delete a prior week
to make performance look better. No edits of historical raw baseline evidence.

## Weekly workflow

1. Read the latest Cotton Candy report and follow up every open action by stable
   ID: what was actually implemented, deployment date, outcome, and next decision.
2. Use the Enchanted Havens repository's frozen benchmark protocol and scorer.
   Run 20 unbranded prompts twice in independent fresh consumer-app chats per
   accessible product. Keep Seattle / US English / USD. Preserve actual model,
   mode, search evidence, locale and timestamps. Never replace consumer results
   with this coding assistant/API, regenerate for better results, or bypass quotas.
   Retain every blocked/error row. Meta Muse remains awaiting access by owner choice.
3. Collect authorized Google Search Console and Bing reports: exact complete
   periods, comparable prior periods, site/query/page/country/device filters,
   clicks, impressions, CTR and average position. Separate brand and non-brand.
   Average position is a report metric, not a universal live ranking. Record
   source lag. Pending or inaccessible reports are null, never zero.
4. Review indexing/crawl changes, sitemap consumption, canonical/robots behavior,
   retired inventory, structured-data errors, and field performance where available.
   A successful HTTP fetch is not proof of indexing. Lab and field results differ.
5. Compare only matched prompt/model/mode/scoring cohorts and comparable search
   windows. Report exact denominators and four-week patterns. Label model or
   protocol breaks. Keep optional new exploratory prompts outside the fixed core.
6. Explain the most useful findings: winning/losing query–page combinations,
   actual competing/cited sources, content or corroboration gaps, unsupported facts,
   and qualified booking outcomes. Cite current primary documentation for technical
   SEO advice. Separate observations from hypotheses; avoid causal claims from a
   single before/after change. Do not promise first position or special AI markup.
7. Prioritize three next actions by evidence, business relevance, confidence and
   effort. Each has an exact page/query/issue, owner, next step, success measure,
   review date, status and follow-up. Include proposed copy/implementation details
   when evidence supports them. Ask and wait on uncertain facts. Keep deferred
   GVR, payment policy and personal-story decisions unchanged. A report authorizes
   measurement and proposals, not automatic website/channel changes or paid access.
8. Publish a new weekly record even when partially blocked, with candid limitations.
   A repeat run in the same week is a new revision, not an extra week. Verify the
   list and the detail record, then notify Richard with its link and the top actions.
   Do not repeatedly notify unchanged access blockers in isolation.

## Producer contract

The exact schema is validated in `service.validate`. Start a narrative JSON with
schema_version 1, site, Monday week_start, baseline/weekly kind,
complete/partial/blocked status, title, summary, aware observed_start/observed_end,
protocol, comparison `{basis: baseline/matched/incomparable, note}`,
provider_notes keyed chatgpt/gemini/claude/muse, search_metrics, insights, actions,
changes, limitations and sources. Inspect the validator for per-field limits.
Use only reviewed evidence; never upload credentials, account emails, browser
session identifiers, guest details or unredacted network logs.

`scripts/build_visibility_report.py` builds the providers/samples fields from
the existing `run.json`, `scorecard.json` and raw answer files. It preserves
schema-1 legacy citation rates with an explicit label. New schema-2 rates include
only complete citation captures. Unknown numerator with denominator zero is null;
a measured zero is explicitly 0 with a positive denominator.

```sh
python scripts/build_visibility_report.py --narrative /private/narrative.json \
  --run /private/dated-benchmark-run --output /private/weekly-report.json
python scripts/visibility_report.py validate --file /private/weekly-report.json
```

Combine provider runs only for a clearly disclosed baseline, using
`--provider-run gemini=/private/older-run`. Never silently carry an older score
forward as a new week's result. A weekly report must account for every planned
sample, including blockers, and all scored answers require captured evidence.

Publish using existing authorized deployment access, running as the existing
application service account with its established environment:

```sh
python scripts/visibility_report.py migrate   # once per approved deployment
python scripts/visibility_report.py list
python scripts/visibility_report.py publish --file /private/weekly-report.json \
  --key eh-visibility-YYYY-MM-DD-r1 --expected-revision 0
```

The publisher records its automation identity; it does not impersonate a human.
No new credentials are required. The authenticated HTTP equivalent is
`POST /visibility/api/reports` with `{report, expected_revision}` and
`Idempotency-Key`. Existing API keys may be passed through the existing header
mechanism; never create or expand a key for this workflow. GET list and detail
APIs mirror `/visibility/api/reports` and `/visibility/api/reports/{id}`.

## Validation and deployment

Unit/integration: `python -m pytest dashboard/visibility/tests/test_reports.py -q`.
Real disposable PostgreSQL: `python -m dashboard.visibility.tests.postgres_qa`.
Browser verification must use the actual UI for summary → detail → answer/source
drill-down. Rendered model HTML must remain escaped. Test nulls, missing products,
history, private reads, CSRF, existing-key scope and actor checks.

The production application includes unrelated local overlays. Do not deploy the
whole checkout or reset it. Back up exact preimages; deploy only new feature files
and the two additive blueprint/navigation hunks against current deployed files.
Abort on preimage drift. Run the explicit additive migration, reload only the
dashboard, verify health/authenticated UI, and preserve the backup for rollback.
The new table can remain during a code rollback; do not delete measured reports.
