"""Data-backed operational proxies. Never promote assumptions to verified actuals."""
from collections import Counter, defaultdict
from datetime import timedelta
from statistics import median
import math
import re
from zoneinfo import ZoneInfo

from dashboard.kpi.operations import ACTIVE_ISSUES, COVERAGE_TARGETS, RATING_TARGETS, as_utc, channel, day, exception, metric, months_before, qualifying, ratio, timed_messages, reply_pair_record, late_reply_summary
from sync.listing_timezone import valid_timezone_name

# Restrict fallback to unambiguous jurisdictions and explicit city mappings.
# Do not reuse the older general helper's silent UTC / split-state defaults.
STATE_ZONES = {"CA": "America/Los_Angeles", "GA": "America/New_York", "OH": "America/New_York", "WA": "America/Los_Angeles"}
CITY_ZONES = {("TN", "sevierville"): "America/New_York", ("TN", "pigeon forge"): "America/New_York", ("TN", "gatlinburg"): "America/New_York", ("TX", "houston"): "America/Chicago", ("TX", "coldspring"): "America/Chicago"}
PORTFOLIO_CODES = {"Enchanted Havens": "EH", "Luminary Resorts": "LR", "Middlefork": "MF", "Crockett’s Run": "CR", "LA St Gabe": "SG", "Urban Stays": "US"}
REVIEW_WINDOW_DAYS = {"airbnb": 14, "vrbo": 365, "booking": 90}
BOOKING_ESTIMATE_POLICY = "known-components-unknown-tax.v2"


def timezone_for_listing(row):
    explicit = valid_timezone_name(row.get("timezone"))
    if explicit:
        return explicit, "Synchronized Hostaway IANA timezone"
    if str(row.get("country") or "").lower() not in {"us", "usa", "united states", "united states of america"}:
        return None, "Timezone unavailable; country/jurisdiction not established"
    state, city = str(row.get("state") or "").strip().upper(), str(row.get("city") or "").strip().lower()
    zone = CITY_ZONES.get((state, city)) or STATE_ZONES.get(state)
    return zone, f"Estimated from synchronized listing jurisdiction: {city}, {state}" if zone else "Timezone unavailable; jurisdiction spans zones or is not mapped"


def numeric(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError): return None


def blank_amount(value):
    return value is None or isinstance(value, str) and not value.strip()


def estimated(key, label):
    r = metric(key)
    r.update(data_status="estimated", goal_status="unknown", label=label, observation=None, display="Not enough data")
    return r


def estimate_revenue(data, windows):
    r = estimated("revenue", "common listings at ≥10% estimated growth")
    sums, covered, excluded, daily = defaultdict(float), defaultdict(int), Counter(), {}
    unknown_tax, rejected, bundles = defaultdict(set), defaultdict(set), defaultdict(set)
    unknown_tax_values = defaultdict(float)
    rows = {x["id"]: x for x in data.get("reservations", [])}
    booking_listings = defaultdict(set)
    for x in rows.values():
        if x.get("channel_booking_id"):
            booking_listings[(channel(x.get("channel")), x["channel_booking_id"])].add(x["listing_id"])
    for x in rows.values():
        w = windows.get(x["listing_id"])
        if not w or not qualifying(x): continue
        a, b = day(x["arrival"]), day(x["departure"])
        if b <= a: excluded["invalid dates"] += 1; continue
        periods = [(period, start, end) for period, start, end in (("current", w["start"], w["end"]), ("prior", months_before(w["start"], 12), months_before(w["end"], 12))) if a < end and b > start]
        if not periods: continue
        if x.get("channel_booking_id") and len(booking_listings[(channel(x.get("channel")), x["channel_booking_id"])]) > 1:
            excluded["shared booking across listings / bundle ambiguity"] += 1
            for period, _, _ in periods: bundles[period].add(x["id"])
            continue
        total, tax, clean, deposit = (numeric(x.get(k)) for k in ("total_price", "tax_amount", "cleaning_fee", "security_deposit_fee"))
        tax_unknown = blank_amount(x.get("tax_amount"))
        reasons = []
        if x.get("currency") != "USD": reasons.append("non_USD_currency")
        if total is None: reasons.append("missing_or_invalid_total")
        if clean is None: reasons.append("missing_or_invalid_cleaning")
        if tax is None and not tax_unknown: reasons.append("invalid_nonblank_tax")
        if deposit is None and not blank_amount(x.get('security_deposit_fee')): reasons.append('invalid_nonblank_deposit')
        if reasons:
            for period, _, _ in periods: rejected[period].add(x["id"])
            for reason in reasons: excluded[reason] += 1
            continue
        # A missing tax remains unknown in the source. This proxy subtracts only
        # recorded components; it does not assert that unrecorded tax is zero.
        amount = total - clean - (deposit if deposit is not None else 0)
        if tax is not None: amount -= tax
        if deposit is None: excluded["deposit component unknown (not reconciled)"] += 1
        if amount < 0:
            excluded["negative_component_adjusted_value"] += 1
            for period, _, _ in periods: rejected[period].add(x["id"])
            continue
        for period, start, end in periods:
            overlap = max(0, (min(b, end) - max(a, start)).days)
            sums[(x["listing_id"], period)] += amount * overlap / (b - a).days
            covered[(x["listing_id"], period)] += 1
            if tax_unknown:
                unknown_tax[period].add(x["id"])
                unknown_tax_values[(x["listing_id"], period)] += amount * overlap / (b - a).days
            d = max(a, start)
            while d < min(b, end):
                key = (x["listing_id"], period, d)
                daily[key] = daily.get(key, 0.0) + amount / (b - a).days
                d += timedelta(days=1)
    met, common, cohort = 0, 0, set()
    for listing in data.get("listings", []):
        lid = listing["id"]
        prior, current = sums[(lid, "prior")], sums[(lid, "current")]
        if not covered[(lid, "prior")] or not covered[(lid, "current")] or prior <= 0: continue
        growth = (current / prior - 1) * 100
        cohort.add(lid)
        common += 1; met += growth >= 10 - 1e-9
        r["breakdown"].append({"group": listing["name"], "estimated_growth_pct": round(growth, 2), "current_proxy_usd": round(current, 2), "prior_proxy_usd": round(prior, 2), "target_pct": 10, "covered_bookings": f"{covered[(lid, 'current')]} current / {covered[(lid, 'prior')]} prior"})
    r["breakdown"].sort(key=lambda x: x["estimated_growth_pct"])
    if common: r.update(value=ratio(met, common), display=f"{met} / {common}", numerator=met, denominator=common)
    r.update(numerator_label="Common listings with estimated growth ≥10%", denominator_label="Listings with usable bookings in both windows; physical cohort unverified", summary="Booking totals less known components; unknown tax may remain included. Refunds are unreconciled and each common listing is compared separately.")
    from dashboard.kpi.revenue_trend import ALIGNMENT, SCHEMA, weekly_comparison
    r["trend"] = weekly_comparison(daily, windows, cohort)
    r["trend_schema"] = SCHEMA
    r["trend_scope"] = f"Same {common} common listing{'s' if common != 1 else ''} as the growth comparison · booking-value estimate before unreconciled refunds; unknown tax may be included."
    r["trend_alignment"] = ALIGNMENT
    r["booking_estimate_policy"] = BOOKING_ESTIMATE_POLICY
    r["financial_coverage"] = {"scope": "Qualifying bookings overlapping current/prior windows across scoped listings, before common-cohort matching", "included_unknown_tax": {period: len(unknown_tax[period]) for period in ("current", "prior")}, "excluded_financial_or_currency": {period: len(rejected[period]) for period in ("current", "prior")}, "shared_booking_exclusions": {period: len(bundles[period]) for period in ("current", "prior")}, "unknown_tax_booking_value_usd": {period: round(sum(value for (lid, p), value in unknown_tax_values.items() if p == period), 2) for period in ("current", "prior")}, "common_cohort_unknown_tax_booking_value_usd": {period: round(sum(value for (lid, p), value in unknown_tax_values.items() if p == period and lid in cohort), 2) for period in ("current", "prior")}, "exclusion_reason_counts": {key: value for key, value in excluded.items() if key not in {"deposit component unknown (not reconciled)", "invalid dates"}}}
    r["diagnostics"] = [{"label": "Common-listing proxy cohort", "value": common or None}, {"label": "Unknown-tax bookings included (scoped current / prior)", "value": f"{len(unknown_tax['current'])} / {len(unknown_tax['prior'])}"}, {"label": "Financial / USD exclusions (scoped current / prior)", "value": f"{len(rejected['current'])} / {len(rejected['prior'])}"}, {"label": "Tax, refunds and discounts", "value": "Unknown tax may remain included; refunds/discounts unreconciled"}, {"label": "Shared-booking bundle exclusions", "value": excluded["shared booking across listings / bundle ambiguity"]}]
    r["method"] = "Booking-value estimate: recorded total less known cleaning/deposit components and tax only when actually recorded, prorated equally over whole-stay nights. A genuinely absent tax (NULL, missing field or blank string) stays unknown and does not exclude the booking; its value may still include tax. Invalid/nonfinite nonblank tax, missing/invalid total or cleaning, non-USD currency and negative component-adjusted values remain excluded. No source field is changed or zero-filled. Financial diagnostic counts cover only overlapping current/prior windows before common-cohort matching. Missing deposits/pass-through components, discount treatment and refunds remain unreconciled. Common listing IDs proxy for comparable physical properties; shared channel booking IDs across listings are excluded for bundle ambiguity. New/unpaired listings are outside the growth comparison. Coverage is the synchronized database, not a certified financial population. Weekly dollar columns use this same fixed common-listing cohort. " + ALIGNMENT
    exception(r, "Reconcile refunds, component inclusion and physical-unit/package mapping before treating this estimate as net lodging revenue.")
    r["observation"] = "Common-listing estimate · unknown tax may be included · refunds unreconciled" if common else "Financial fields insufficient for a paired estimate"
    return r


def rating_scale(row, data):
    """A source-specific evidence contract, never inferred from score magnitude."""
    source = data.get("rating_scales", {}).get(channel(row.get("channel")), {})
    if row.get("scale_verified") and row.get("scale_provenance"):
        scale, proof = numeric(row.get("native_scale")), row["scale_provenance"]
    elif source.get("verified") and source.get("provenance"):
        scale, proof = numeric(source.get("native_scale")), source["provenance"]
    else:
        return None, "Native scale unverified; no conversion or target comparison"
    return (scale, proof) if scale and scale > 0 else (None, "Invalid source-scale contract")


def estimate_reviews(data, windows):
    r = estimated("reviews", "Airbnb rolling-checkout coverage (provisional)")
    reviews = {x["id"]: x for x in data.get("reviews", []) if str(x.get("status") or "").lower() == "published" and str(x.get("origin") or "").lower() == "guest"}
    linked = defaultdict(list)
    for x in reviews.values(): linked[(x.get("reservation_id"), x["listing_id"], channel(x.get("channel")))].append(x)
    base, n, uncertain, still_open, weeks = defaultdict(set), defaultdict(set), defaultdict(set), defaultdict(set), defaultdict(lambda: [set(), set()])
    review_days = {}
    for ch, assumed in REVIEW_WINDOW_DAYS.items():
        policy = data.get("review_policies", {}).get(ch, {})
        days = numeric(policy.get("review_window_days")) if policy.get("verified") else None
        review_days[ch] = (days, "Verified source policy") if days is not None and days >= 0 else (assumed, "Assumed review window")
    for x in {x["id"]: x for x in data.get("reservations", [])}.values():
        w, ch = windows.get(x["listing_id"]), channel(x.get("channel"))
        if not w or ch not in REVIEW_WINDOW_DAYS: continue
        checkout = day(x["departure"])
        if not w["start"] <= checkout < w["end"]: continue
        has_review = any(v.get("date") and checkout <= day(v["date"]) < w["end"] for v in linked[(x["id"], x["listing_id"], ch)])
        if qualifying(x) or has_review:
            base[ch].add(x["id"])
            key = (ch, checkout - timedelta(days=checkout.weekday()))
            weeks[key][1].add(x["id"])
            if has_review: n[ch].add(x["id"]); weeks[key][0].add(x["id"])
            elif checkout + timedelta(days=review_days[ch][0]) >= w["end"]: still_open[ch].add(x["id"])
        elif "cancel" in str(x.get("status", "")).lower() and (not x.get("cancellation_date") or day(x["cancellation_date"]) >= day(x["arrival"])):
            uncertain[ch].add(x["id"])
    for ch, target in COVERAGE_TARGETS.items():
        count, num = len(base[ch]), len(n[ch])
        value = ratio(num, count)
        low = ratio(num, count + len(uncertain[ch]))
        r["breakdown"].append({"group": ch.title(), "measure": "Rolling-checkout coverage (provisional)", "value": f"{value:g}%" if value is not None else None, "target": f"≥{target}%", "reviewed": num, "unreviewed": count - num, "still_open_opportunities": len(still_open[ch]), "unreviewed_window_elapsed": count - num - len(still_open[ch]), "opportunities": count, "review_window": f"{review_days[ch][0]:g} days · {review_days[ch][1]}", "sensitivity": f"{low:g}–{value:g}% if unresolved cancellations are eligible" if count and uncertain[ch] else "No observed checkout cohort" if not count else "Cancellation sensitivity unchanged"})
    if base["airbnb"]:
        value = ratio(len(n["airbnb"]), len(base["airbnb"]))
        r.update(value=value, display=f"{value:g}%", numerator=len(n["airbnb"]), denominator=len(base["airbnb"]))
    listings = {x["id"]: x for x in data.get("listings", []) + data.get("historical_listings", [])}
    ratings, proofs = defaultdict(list), {}
    for x in reviews.values():
        w, score = windows.get(x["listing_id"]), numeric(x.get("rating"))
        code = PORTFOLIO_CODES.get(listings.get(x["listing_id"], {}).get("portfolio"))
        if w and code and score is not None and score > 0 and x.get("date") and w["start"] <= day(x["date"]) < w["end"]:
            scale, proof = rating_scale(x, data)
            if scale and score > scale:
                scale, proof = None, "Value exceeds the verified scale; source reconciliation required"
            key = (code, channel(x.get("channel")), scale)
            ratings[key].append(score)
            proofs[key] = proof
    for code, target in RATING_TARGETS.items():
        keys = [key for key in ratings if key[0] == code]
        if not keys:
            r["breakdown"].append({"group": code, "measure": "Native guest rating", "value": None, "native_scale": "Unknown", "target": f">{target:g} /5", "coverage": "No observed rated reviews"})
        for key in sorted(keys, key=lambda x: (x[1], x[2] or 0)):
            _, ch, scale = key
            values = ratings[key]
            r["breakdown"].append({"group": code, "channel": ch.title(), "measure": "Native guest rating" if scale else "Observed native rating · scale unknown", "value": round(sum(values) / len(values), 3), "native_scale": scale or "Unknown", "target": f">{target:g} /5" if scale == 5 else f">{target:g} /5 · not comparable", "coverage": f"{len(values)} rated reviews", "scale_provenance": proofs[key], "comparison": "Portfolio mapping unverified; no compliance claim"})
    r["trend"] = [{"date": d.isoformat(), "group": ch.title(), "value": ratio(len(a), len(b)), "reviewed": len(a), "checkout_opportunities": len(b), "target": COVERAGE_TARGETS[ch]} for (ch, d), (a, b) in sorted(weeks.items())]
    r.update(numerator_label="Observed published guest reviews linked to Airbnb checkouts", denominator_label="Observed booked or review-proven Airbnb opportunities, including still-open windows", summary="Provisional rolling-checkout coverage includes open opportunities. Ratings remain native and separate by channel and verified scale.")
    r["diagnostics"] = [{"label": "Airbnb reviewed / checkout opportunities", "value": f"{len(n['airbnb'])}/{len(base['airbnb'])}" if base["airbnb"] else None}, {"label": "Airbnb unreviewed / still open", "value": f"{len(base['airbnb'])-len(n['airbnb'])} / {len(still_open['airbnb'])}" if base["airbnb"] else None}, {"label": "Airbnb unresolved cancellation opportunities", "value": len(uncertain["airbnb"])}, {"label": "Historical publication visibility", "value": "Unverified; current published state / synchronized review date"}]
    r["method"] = "Coverage uses every observed booked or review-proven checkout in the local three-month window, including its first day and archived listings. It does not discard recent checkouts to wait for full maturation. Reviewed, unreviewed and still-open opportunities are separate; open-window flags use a verified source policy when supplied, otherwise assumed Airbnb 14, Vrbo 365 and Booking 90 days. This is provisional coverage, not final missed-review performance. Canceled stays with matching published reviews count as observed opportunities. Unreviewed after-arrival/unknown-timing cancellations form a conditional sensitivity range, not a confidence interval. Ratings use a separate review-date cohort and stay in their native scale, grouped by channel and source-scale contract; unknown or non-five-point scales are not compared with five-point targets or automatically converted. Actual stayed status, historical publication visibility and tag-derived portfolio mapping remain uncertain."
    exception(r, "Review cancellation eligibility, historical publication visibility, portfolio mapping and native rating scales before certification.")
    r["observation"] = "Provisional coverage · open review opportunities included · native rating scales"
    return r


REQUEST = re.compile(r"\?|\b(where|when|how|can|could|please|help|problem|broken|dirty|missing|issue|code|password|not working)\b", re.I)
ACK = re.compile(r"^(thanks?([ !.,]|$)|thank you|got it|received|we.?ll (get back|respond)|your message has been received|we received your message)", re.I)
STOP = {"the", "and", "you", "your", "our", "for", "can", "could", "please", "have", "with", "this", "that", "will", "would", "there", "what", "where", "when", "how", "are", "was", "not", "but", "from", "just", "message", "hello", "hi", "thanks"}
TOPICS = {"wifi": r"wifi|wi-fi|internet|password", "entry": r"check.?in|entry|access|door|lock|code|arriv", "clean": r"clean|dirty|stain|towel|linen|trash", "water": r"water|shower|plumb|toilet", "temperature": r"heat|cold|hvac|air condition", "parking": r"park|garage", "pool": r"pool|hot.?tub|spa", "checkout": r"check.?out|depart|leav"}


def tokens(text): return set(re.findall(r"[a-z]{3,}", text.lower())) - STOP
def topics(text): return {key for key, pattern in TOPICS.items() if re.search(pattern, text, re.I)}


def estimate_response(data, windows, as_of):
    r = estimated("response", "recorded-message reply timing proxy")
    grouped, durations, groups, weeks = defaultdict(list), [], defaultdict(list), defaultdict(list)
    matched, pending_count, request_count = 0, 0, 0
    messages, untimed = timed_messages(data.get("messages", []))
    names = {x["id"]: x["name"] for x in data.get("listings", [])}
    late_pairs = []
    for x in messages: grouped[x["conversation_id"]].append(x)
    for messages in grouped.values():
        pending, request_text = None, ""
        for x in sorted(messages, key=lambda x: (as_utc(x["at"]), x["id"])):
            at, body = as_utc(x["at"]), str(x.get("body") or "")
            w = windows.get(x["listing_id"])
            if not w or at > as_of: continue
            local_day = at.astimezone(ZoneInfo(w["timezone"])).date()
            if not w["start"] <= local_day < w["end"]: continue
            if x.get("incoming"):
                if REQUEST.search(body) and not ACK.search(body):
                    if pending is None: pending = x; request_count += 1
                    request_text += " " + body
                continue
            if pending is None or len(body.strip()) < 15 or ACK.search(body): continue
            relevant = bool(topics(body) & topics(request_text)) or len(tokens(body) & tokens(request_text)) >= 2
            if not relevant: continue
            elapsed = (at - as_utc(pending["at"])).total_seconds() / 60
            if elapsed < 0: continue
            durations.append(elapsed); groups[x["listing_id"]].append(elapsed)
            if elapsed >= 30: late_pairs.append(reply_pair_record(pending, x, elapsed, windows, names))
            d = as_utc(pending["at"]).astimezone(ZoneInfo(w["timezone"])).date()
            weeks[d - timedelta(days=d.weekday())].append(elapsed)
            matched += 1; pending = None; request_text = ""
        pending_count += pending is not None
    if durations:
        value = round(median(durations), 2)
        r.update(value=value, display=f"{value:g} min", numerator=sum(x < 5 for x in durations), denominator=len(durations))
    r["late_replies_30m"] = late_reply_summary(durations, late_pairs, pending_count, untimed, basis="matched_recorded_timing_proxy", unplaced_requests=sum(bool(x.get("incoming")) and bool(REQUEST.search(str(x.get("body") or ""))) and not bool(ACK.search(str(x.get("body") or ""))) for x in untimed))
    r["breakdown"] = [{"group": names.get(lid, str(lid)), "median_proxy_minutes": round(median(v), 2), "under_5_pct": ratio(sum(x < 5 for x in v), len(v)), "matched_pairs": len(v)} for lid, v in groups.items()]
    r["breakdown"].sort(key=lambda x: -x["median_proxy_minutes"])
    r["trend"] = [{"date": d.isoformat(), "value": round(median(v), 2), "sample": len(v), "target": 5} for d, v in sorted(weeks.items())]
    r.update(numerator_label="Heuristically relevant recorded outbound pairs under 5 minutes", denominator_label="Matched recorded request/answer pairs (not a verified population)", summary="Recorded timestamps plus a relevance heuristic; exact guest-origin-to-meaningful-dispatch SLA remains unknown.")
    r["diagnostics"] = [{"label": "Heuristically matched pairs / request sequences", "value": f"{matched}/{request_count}"}, {"label": "Under 5 minutes in this matched sample", "value": f"{ratio(sum(x < 5 for x in durations), len(durations))}%" if durations else None}, {"label": "Unmatched request candidates (not verified misses)", "value": pending_count}, {"label": "Answer quality / dispatch verification", "value": "Not established by this timing proxy"}]
    r["method"] = "A request heuristic groups consecutive question/problem messages. The next non-generic outbound sharing a relevant topic or at least two meaningful words forms a timing pair, including triggered instructions when relevant. This uses stored Hostaway API date timestamps; it does not establish original guest send or sentChannelDate dispatch. Generic acknowledgements and unrelated schedules are excluded heuristically. Missing matches are not confirmed missed requests; carry-in and source-window coverage are incomplete. No external model or source API is called by the calculation."
    exception(r, "Inspect slow matched pairs and unmatched candidates in the original conversations; do not treat missing dispatch evidence as a missed request.")
    r["observation"] = f"{matched} heuristic timing pairs · exact SLA unknown"
    return r


def issue_identity(x):
    if x.get("dedupe_key"): return ("saved", x["dedupe_key"])
    if x.get("complaint_key") and x.get("reservation_id"): return ("complaint", x["listing_id"], x["reservation_id"], x["complaint_key"])
    return ("source", x["id"])


def estimate_issues(data):
    r = estimated("issues", "operationally active guest-issue records / identities")
    r["data_status"] = "observed"
    groups, conflicts = defaultdict(list), 0
    for x in data.get("issues", []):
        if x.get("operational_status") != "resolved": groups[issue_identity(x)].append(x)
    counts = Counter()
    for rows in groups.values():
        counts[rows[0]["listing_id"]] += 1
        conflicts += any(x.get("resolution_state") == "resolved" or x.get("workflow_status") == "resolved" for x in rows)
    n = len(groups) if data.get("issues_readable") else None
    proxy = len(data.get("listings", [])) or None
    if n is not None: r.update(value=n, display=str(n), numerator=n, denominator=proxy)
    r["breakdown"] = [{"group": str(lid), "active_identities": count} for lid, count in counts.most_common()]
    r.update(target="Provisional comparison: active load ≤ catalog entries (unit proxy)", visual_label=f"{n} active / {proxy} catalog entries (unit proxy) · provisional" if n is not None and proxy else None, numerator_label="Operationally active guest issues, including old unresolved carry-ins", denominator_label="Catalog entries (unit proxy); physical inventory unverified", summary="Current operational load; comparison with catalog entries is provisional. Physical-unit compliance is unverified.")
    r["diagnostics"] = [{"label": "Catalog entries used as unit proxy", "value": proxy}, {"label": "Active / narrative-resolved conflicts retained", "value": conflicts}, {"label": "Conditional load if all such conflicts were resolved", "value": f"{n-conflicts}–{n}" if n is not None else None}, {"label": "Historical daily backlog", "value": "Unavailable; no synthetic series"}]
    r["method"] = "Count all operationally active or unknown-status guest issues across source dates, including carry-ins. Consolidate only exact saved dedupe identities or matching property/reservation/complaint keys; possible text duplicates are not merged. Narrative-resolved conflicts remain active. The sensitivity range conditionally removes those conflicts and is not a confidence interval. Current catalog entries proxy for units; archive/scope and bundle mapping are not a physical-unit register."
    exception(r, "Reconcile active/narrative-resolved conflicts and confirm the physical-unit count before certifying the target.")
    r["observation"] = f"{proxy} catalog entries as unit proxy · physical count unverified" if proxy else None
    return r


CLEANING_STRICT = {"cleanliness", "housekeeping", "deep_cleaning", "cleaning_readiness", "cleanliness_turnover", "turnover_readiness", "linen_cleanliness", "dishware_cleanliness", "kitchen_cleanliness", "appliance_cleanliness", "pool_cleanliness", "hot_tub_cleanliness", "outdoor_cleanliness", "exterior_cleanliness", "yard_cleanliness", "outdoor_amenity_cleanliness"}
CLEANING_AMBIGUOUS = {"linens", "linens_and_bedding", "linen_condition", "missing_linens", "laundry", "turnover_laundry", "laundry_readiness", "odor", "odor_smoke", "odor_mildew", "pests", "pest_infestation", "trash", "waste_management", "cleaning_supplies", "accuracy_and_cleanliness_signal"}


def estimate_cleaning(data, windows):
    r = estimated("cleaning", "affected stays per 10 check-ins (estimate)")
    checkins = {x["id"]: x for x in data.get("reservations", []) if qualifying(x) and windows.get(x["listing_id"]) and windows[x["listing_id"]]["start"] <= day(x["arrival"]) < windows[x["listing_id"]]["end"]}
    strict, possible, unmatched = set(), set(), 0
    identities, strict_identities, strict_records = set(), set(), set()
    identities_by_listing, strict_identities_by_listing, records_by_listing = defaultdict(set), defaultdict(set), defaultdict(set)
    names = {row['id']: str(row.get('name') or row['id']) for row in data.get('listings', [])}
    references = []
    # Includes resolved and active issues; this is an incidence metric, not backlog.
    for x in {x["id"]: x for x in data.get("issues", [])}.values():
        category = str(x.get("category") or "").lower()
        if category not in CLEANING_STRICT | CLEANING_AMBIGUOUS: continue
        reservation = checkins.get(x.get("reservation_id"))
        if not reservation or reservation["listing_id"] != x["listing_id"]:
            w = windows.get(x["listing_id"])
            if w and x.get("date") and w["start"] <= day(x["date"]) < w["end"]: unmatched += 1
            continue
        identity = issue_identity(x)
        checkin = day(reservation['arrival'])
        references.append({'issue_id': str(x['id']), 'listing_id': x['listing_id'], 'property': names.get(x['listing_id'], str(x['listing_id'])), 'checkin_date': checkin.isoformat(), 'week': (checkin-timedelta(days=checkin.weekday())).isoformat(), 'category': category, 'category_mapping': 'Strict mapped' if category in CLEANING_STRICT else 'Ambiguous candidate', 'source_view': 'resolved' if x.get('workflow_status') == 'resolved' else 'active'})
        possible.add(x["reservation_id"]); identities.add(identity)
        identities_by_listing[x["listing_id"]].add(identity)
        if category in CLEANING_STRICT:
            strict.add(x["reservation_id"])
            strict_identities.add(identity); strict_records.add(x["id"])
            strict_identities_by_listing[x["listing_id"]].add(identity)
            records_by_listing[x["listing_id"]].add(x["id"])
    if checkins and data.get("issues_readable") and (possible or not unmatched):
        value = ratio(len(strict), len(checkins), 10)
        r.update(value=value, display=f"{value:g}", numerator=len(strict), denominator=len(checkins))
    identity_rate = ratio(len(strict_identities), len(checkins), 10)
    record_rate = ratio(len(strict_records), len(checkins), 10)
    inclusive_rate = ratio(len(identities), len(checkins), 10)
    for lid in windows:
        ids = {key for key, x in checkins.items() if x["listing_id"] == lid}
        if ids:
            r["breakdown"].append({"group": str(lid), "affected_checkins": len(strict & ids), "booked_checkins": len(ids), "affected_checkin_proxy_per_10": ratio(len(strict & ids), len(ids), 10), "strict_saved_identities": len(strict_identities_by_listing[lid]), "strict_source_records": len(records_by_listing[lid]), "saved_identity_scenario_per_10": ratio(len(strict_identities_by_listing[lid]), len(ids), 10), "source_record_scenario_per_10": ratio(len(records_by_listing[lid]), len(ids), 10), "including_ambiguous_identity_per_10": ratio(len(identities_by_listing[lid]), len(ids), 10)})
    r["breakdown"].sort(key=lambda x: -x["affected_checkin_proxy_per_10"])
    weeks = defaultdict(set)
    property_weeks = defaultdict(set)
    for w in windows.values():
        monday=w['start']-timedelta(days=w['start'].weekday())
        while monday<w['end']:
            weeks[monday]
            monday+=timedelta(days=7)
    for lid,w in windows.items():
        monday=w['start']-timedelta(days=w['start'].weekday())
        while monday<w['end']:
            property_weeks[(lid,monday)]
            monday+=timedelta(days=7)
    for key, x in checkins.items():
        d = day(x["arrival"]); bucket = d - timedelta(days=d.weekday())
        weeks[bucket].add(key);property_weeks[(x['listing_id'],bucket)].add(key)
    readable_checkins = 'reservations' in data and data.get('sources',{}).get('reservations',{}).get('status') not in {'error','missing'}
    def weekly_row(monday,ids,lids):
        denominator=len(ids) if readable_checkins else None
        numerator=len(strict&ids) if readable_checkins and data.get('issues_readable') else None
        percentage=ratio(numerator,denominator) if numerator is not None else None
        days=sorted({(min(monday+timedelta(days=7),windows[lid]['end'])-max(monday,windows[lid]['start'])).days for lid in lids if max(monday,windows[lid]['start'])<min(monday+timedelta(days=7),windows[lid]['end'])})
        return {'date':monday.isoformat(),'value':percentage,'total_stays':denominator,'affected_stays':numerator,'ambiguous_only_stays':len((possible-strict)&ids) if readable_checkins and data.get('issues_readable') else None,'partial_week':any(n<7 for n in days),'days_per_property':days}
    r['trend']=[weekly_row(d,ids,windows.keys()) for d,ids in sorted(weeks.items())]
    property_rows=[dict(weekly_row(d,ids,[lid]),listing_id=lid) for (lid,d),ids in sorted(property_weeks.items())]
    references.sort(key=lambda row:(row['checkin_date'],row['issue_id']),reverse=True)
    properties=[]
    for lid in sorted(windows):
        ids={key for key,row in checkins.items() if row['listing_id']==lid}
        numerator=len(strict&ids) if data.get('issues_readable') and readable_checkins else None
        properties.append({'listing_id':lid,'name':names.get(lid,str(lid)),'total_stays':len(ids) if readable_checkins else None,'affected_stays':numerator,'percentage':ratio(numerator,len(ids)) if numerator is not None else None,'source_issue_count':sum(row['listing_id']==lid for row in references) if data.get('issues_readable') else None})
    # Keep at least one recent reference per property before filling the cap.
    chosen={}
    for lid in sorted(windows):
        first=next((row for row in references if row['listing_id']==lid),None)
        if first: chosen[first['issue_id']]=first
    for row in references:
        if len(chosen)>=200: break
        chosen[row['issue_id']]=row
    total=len(checkins) if readable_checkins and windows else None
    affected=len(strict) if total is not None and data.get('issues_readable') else None
    percentage=ratio(affected,total) if affected is not None else None
    r['cleaning_weekly']={'schema':'affected-stay-share.v3','counts':{'total_stays':total,'affected_stays':affected,'percentage':percentage},'properties':properties,'property_rows':property_rows,'references':list(chosen.values()),'reference_count':len(references),'reference_limit':200,'ambiguous_only_stays':len(possible-strict) if data.get('issues_readable') and readable_checkins else None,'unmatched_issue_records':unmatched if data.get('issues_readable') else None}
    r.update(target="Goal: <1 distinct incident per 10 check-ins · proxy comparison provisional", numerator_label="Booked check-ins with cleaning-tagged issues, active or resolved (not distinct incidents)", denominator_label="Observed qualifying booked check-ins; actual arrival and package identity unverified", summary="Affected-check-in proxy; multiple incidents in one stay may be combined. Distinct-incident compliance remains unverified.")
    r["diagnostics"] = [{"label": "Strict affected check-ins / saved identities / source records", "value": f"{len(strict)} / {len(strict_identities)} / {len(strict_records)}"}, {"label": "Per 10 scenarios: affected check-ins / saved identities / source records", "value": f"{r['value']:g} / {identity_rate:g} / {record_rate:g}" if r["value"] is not None else None}, {"label": "In-window candidates not joined to eligible check-ins", "value": unmatched}, {"label": "Including ambiguous identities per 10 (conditional)", "value": inclusive_rate}, {"label": "Issue statuses included", "value": "Active and resolved"}]
    r["method"] = "The main value measures booked check-ins affected by explicit cleaning/housekeeping/cleanliness tags, not distinct incidents. Multiple complaints or incidents in one reservation contribute one affected check-in. Separate scenarios count strict saved complaint identities and strict source records; neither establishes validated distinct incidents. An additional scenario includes ambiguous linens, laundry, odors, pests and waste identities. Scenarios are conditional assumptions, not confidence intervals or exhaustive bounds. Only exact saved complaint identities are consolidated; possible text duplicates are not merged. Active and resolved records are included. Candidates lacking an eligible reservation/listing join are excluded and reported. Booked check-ins proxy for actual arrivals; package duplicates and classification coverage remain unverified. The original distinct-incident goal is retained and is not scored by this proxy."
    exception(r, "Review ambiguous categories and unmatched check-in joins; validate distinct incidents and actual arrivals before certification.")
    r["observation"] = f"{len(strict)} affected check-ins · {len(strict_identities)} strict identities · {len(strict_records)} source records" if checkins else "No usable check-in denominator"
    r['audit_rate_per_10']=r['value']
    r['original_goal_target']=r['target']
    r.update(value=percentage,display=f'{affected} of {total}' if affected is not None and total else 'No stay data' if total==0 else 'Unavailable',label='stays had cleaning issues',numerator=affected,denominator=total,target='Track affected stays versus total stays',summary='Each stay counts once, even with multiple recorded cleaning issues.',observation=f'{percentage:g}% of recorded stays' if percentage is not None else None)
    return r


def apply_estimates(metrics, data, windows, review_windows, as_of):
    replacements = {"revenue": lambda: estimate_revenue(data, windows), "reviews": lambda: estimate_reviews(data, review_windows), "response": lambda: estimate_response(data, windows, as_of), "issues": lambda: estimate_issues(data), "cleaning": lambda: estimate_cleaning(data, windows)}
    result = []
    for m in metrics:
        if m["key"] == "content":
            m = metric("content")
            m.update(display="Deferred", data_status="deferred", goal_status="deferred", target="Team definition pending", summary="Listing refresh and edit-history tracking are deferred for team discussion.", observation=None)
        elif m["data_status"] != "verified" or m['key'] == 'cleaning': m = replacements[m["key"]]()
        result.append(m)
    return result
