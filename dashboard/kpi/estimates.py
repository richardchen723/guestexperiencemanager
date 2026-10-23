"""Data-backed operational proxies. Never promote assumptions to verified actuals."""
from collections import Counter, defaultdict
from datetime import timedelta
from statistics import median
import math
import re
from zoneinfo import ZoneInfo

from dashboard.kpi.operations import ACTIVE_ISSUES, COVERAGE_TARGETS, RATING_TARGETS, as_utc, channel, day, exception, metric, months_before, qualifying, ratio
from sync.listing_timezone import valid_timezone_name

# Restrict fallback to unambiguous jurisdictions and explicit city mappings.
# Do not reuse the older general helper's silent UTC / split-state defaults.
STATE_ZONES = {"CA": "America/Los_Angeles", "GA": "America/New_York", "OH": "America/New_York", "WA": "America/Los_Angeles"}
CITY_ZONES = {("TN", "sevierville"): "America/New_York", ("TN", "pigeon forge"): "America/New_York", ("TN", "gatlinburg"): "America/New_York", ("TX", "houston"): "America/Chicago", ("TX", "coldspring"): "America/Chicago"}
PORTFOLIO_CODES = {"Enchanted Havens": "EH", "Luminary Resorts": "LR", "Middlefork": "MF", "Crockett’s Run": "CR", "LA St Gabe": "SG", "Urban Stays": "US"}
MATURATION_DAYS = {"airbnb": 14, "vrbo": 365, "booking": 90}


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


def estimated(key, label):
    r = metric(key)
    r.update(data_status="estimated", goal_status="unknown", label=label, observation=None, display="Not enough data")
    return r


def estimate_revenue(data, windows):
    r = estimated("revenue", "common listings at ≥10% estimated growth")
    sums, covered, excluded, weekly = defaultdict(float), defaultdict(int), Counter(), defaultdict(float)
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
        if x.get("channel_booking_id") and len(booking_listings[(channel(x.get("channel")), x["channel_booking_id"])]) > 1:
            excluded["shared booking across listings / bundle ambiguity"] += 1; continue
        total, tax, clean, deposit = (numeric(x.get(k)) for k in ("total_price", "tax_amount", "cleaning_fee", "security_deposit_fee"))
        if x.get("currency") != "USD" or total is None or tax is None or clean is None:
            excluded["missing tax/cleaning/total or non-USD"] += 1; continue
        amount = total - tax - clean - (deposit if deposit is not None else 0)
        if deposit is None: excluded["deposit component unknown (not reconciled)"] += 1
        if amount < 0: excluded["negative component-adjusted value"] += 1; continue
        for period, start, end in (("current", w["start"], w["end"]), ("prior", months_before(w["start"], 12), months_before(w["end"], 12))):
            overlap = max(0, (min(b, end) - max(a, start)).days)
            if not overlap: continue
            sums[(x["listing_id"], period)] += amount * overlap / (b - a).days
            covered[(x["listing_id"], period)] += 1
            if period == "current":
                d = max(a, start)
                while d < min(b, end):
                    weekly[d - timedelta(days=d.weekday())] += amount / (b - a).days
                    d += timedelta(days=1)
    met, common = 0, 0
    for listing in data.get("listings", []):
        lid = listing["id"]
        prior, current = sums[(lid, "prior")], sums[(lid, "current")]
        if not covered[(lid, "prior")] or not covered[(lid, "current")] or prior <= 0: continue
        growth = (current / prior - 1) * 100
        common += 1; met += growth >= 10 - 1e-9
        r["breakdown"].append({"group": listing["name"], "estimated_growth_pct": round(growth, 2), "current_proxy_usd": round(current, 2), "prior_proxy_usd": round(prior, 2), "target_pct": 10, "covered_bookings": f"{covered[(lid, 'current')]} current / {covered[(lid, 'prior')]} prior"})
    r["breakdown"].sort(key=lambda x: x["estimated_growth_pct"])
    if common: r.update(value=ratio(met, common), display=f"{met} / {common}", numerator=met, denominator=common)
    r.update(numerator_label="Common listings with estimated growth ≥10%", denominator_label="Listings with usable bookings in both windows; physical cohort unverified", summary="Known-fee-adjusted booking estimate before unreconciled refunds; each common listing is compared separately.")
    r["trend"] = [{"date": d.isoformat(), "value": round(v, 2), "unit": "USD booking proxy"} for d, v in sorted(weekly.items())]
    r["diagnostics"] = [{"label": "Common-listing proxy cohort", "value": common or None}, {"label": "Bookings excluded for incomplete financial fields / USD", "value": excluded["missing tax/cleaning/total or non-USD"]}, {"label": "Shared-booking bundle exclusions", "value": excluded["shared booking across listings / bundle ambiguity"]}, {"label": "Refund and discount reconciliation", "value": "Unknown; this is before unreconciled adjustments"}]
    r["method"] = "Estimate: reservation total less recorded taxes, cleaning and known deposit components; prorated equally over whole-stay nights. Missing tax/cleaning excludes a booking. Missing deposits/pass-through components, discount treatment and refunds remain unreconciled, not assumed settled or zero. Common listing IDs proxy for comparable physical properties; shared channel booking IDs across listings are excluded for bundle ambiguity. New/unpaired listings are outside this comparison. Coverage is the synchronized database, not a certified financial population."
    exception(r, "Reconcile refunds, component inclusion and physical-unit/package mapping before treating this estimate as net lodging revenue.")
    r["observation"] = "Estimated from common listing IDs · before unreconciled refunds" if common else "Financial fields insufficient for a paired estimate"
    return r


def estimate_reviews(data, windows):
    r = estimated("reviews", "Airbnb mature-checkout coverage estimate")
    reviews = {x["id"]: x for x in data.get("reviews", []) if str(x.get("status", "")).lower() == "published" and str(x.get("origin", "")).lower() == "guest"}
    linked = defaultdict(list)
    for x in reviews.values(): linked[(x.get("reservation_id"), x["listing_id"], channel(x.get("channel")))].append(x)
    base, n, uncertain, immature, weeks = defaultdict(set), defaultdict(set), defaultdict(set), Counter(), defaultdict(lambda: [set(), set()])
    for x in {x["id"]: x for x in data.get("reservations", [])}.values():
        w, ch = windows.get(x["listing_id"]), channel(x.get("channel"))
        if not w or ch not in MATURATION_DAYS: continue
        checkout = day(x["departure"])
        if not w["start"] <= checkout < w["end"]: continue
        if checkout > w["end"] - timedelta(days=MATURATION_DAYS[ch]): immature[ch] += 1; continue
        has_review = any(v.get("date") and checkout <= day(v["date"]) < w["end"] for v in linked[(x["id"], x["listing_id"], ch)])
        if qualifying(x) or has_review:
            base[ch].add(x["id"])
            key = (ch, checkout - timedelta(days=checkout.weekday()))
            weeks[key][1].add(x["id"])
            if has_review: n[ch].add(x["id"]); weeks[key][0].add(x["id"])
        elif "cancel" in str(x.get("status", "")).lower() and (not x.get("cancellation_date") or day(x["cancellation_date"]) >= day(x["arrival"])):
            uncertain[ch].add(x["id"])
    for ch, target in COVERAGE_TARGETS.items():
        count, num = len(base[ch]), len(n[ch])
        value = ratio(num, count)
        low = ratio(num, count + len(uncertain[ch]))
        r["breakdown"].append({"group": ch.title(), "measure": "Mature-checkout coverage", "value": f"{value:g}%" if value is not None else None, "target": f"≥{target}%", "coverage": f"{num}/{count}; {immature[ch]} immature", "sensitivity": f"{low:g}–{value:g}% if unresolved cancellations are eligible" if count and uncertain[ch] else "No mature cohort" if not count else "Cancellation sensitivity unchanged"})
    if base["airbnb"]:
        value = ratio(len(n["airbnb"]), len(base["airbnb"]))
        r.update(value=value, display=f"{value:g}%", numerator=len(n["airbnb"]), denominator=len(base["airbnb"]))
    listings = {x["id"]: x for x in data.get("listings", []) + data.get("historical_listings", [])}
    ratings = defaultdict(list)
    for x in reviews.values():
        w, score = windows.get(x["listing_id"]), numeric(x.get("rating"))
        code = PORTFOLIO_CODES.get(listings.get(x["listing_id"], {}).get("portfolio"))
        if w and code and score is not None and 0 < score <= 10 and x.get("date") and w["start"] <= day(x["date"]) < w["end"]:
            ratings[code].append(score / 2)
    for code, target in RATING_TARGETS.items():
        values = ratings[code]
        r["breakdown"].append({"group": code, "measure": "Normalized rating estimate /5", "value": round(sum(values) / len(values), 3) if values else None, "target": f">{target:g}", "coverage": f"{len(values)} rated reviews", "sensitivity": "Assumes PMS overall_rating uses 0–10; native scales unverified"})
    r["trend"] = [{"date": d.isoformat(), "group": ch.title(), "value": ratio(len(a), len(b)), "reviewed": len(a), "mature_checkouts": len(b), "target": COVERAGE_TARGETS[ch]} for (ch, d), (a, b) in sorted(weeks.items())]
    r.update(numerator_label="Observed published guest reviews linked to mature Airbnb checkouts", denominator_label="Observed booked or review-proven mature Airbnb opportunities", summary="Coverage is estimated from mature checkout opportunities; rating estimates are a separate review-date cohort.")
    r["diagnostics"] = [{"label": "Airbnb reviewed / mature checkout opportunities", "value": f"{len(n['airbnb'])}/{len(base['airbnb'])}" if base["airbnb"] else None}, {"label": "Airbnb unresolved cancellation opportunities", "value": len(uncertain["airbnb"])}, {"label": "Maturation assumptions (days)", "value": "Airbnb 14 · Vrbo 365 · Booking 90"}, {"label": "Historical publication visibility", "value": "Unverified; current published state / synchronized review date"}]
    r["method"] = "Coverage uses exact reservation/listing/channel joins and checkouts in the local window, including its first day and archived listings. Assumed maturation: Airbnb 14, Vrbo 365, Booking 90 days. Canceled stays with matching published reviews count as observed opportunities. Unreviewed after-arrival/unknown-timing cancellations form a conditional sensitivity range, not a confidence interval. Actual stayed status and historical publication visibility are uncertain. Ratings use the separate synchronized-review-date cohort, divide assumed PMS 0–10 scores by two, and use tag-derived portfolio labels; this normalized estimate does not verify native channel scales."
    exception(r, "Review cancellation eligibility, historical publication visibility, portfolio mapping and native rating scales before certification.")
    r["observation"] = "Estimated checkout coverage · ratings normalized with stated scale assumption"
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
    for x in {x["id"]: x for x in data.get("messages", [])}.values(): grouped[x["conversation_id"]].append(x)
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
            d = as_utc(pending["at"]).astimezone(ZoneInfo(w["timezone"])).date()
            weeks[d - timedelta(days=d.weekday())].append(elapsed)
            matched += 1; pending = None; request_text = ""
        pending_count += pending is not None
    if durations:
        value = round(median(durations), 2)
        r.update(value=value, display=f"{value:g} min", numerator=sum(x < 5 for x in durations), denominator=len(durations))
    names = {x["id"]: x["name"] for x in data.get("listings", [])}
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
    r.update(numerator_label="Operationally active guest issues, including old unresolved carry-ins", denominator_label="Current catalog listing count: an estimated unit proxy, not physical inventory", summary="Current operational load; only saved complaint identities are consolidated. No historical backlog is inferred.")
    r["diagnostics"] = [{"label": "Catalog entries used as unit proxy", "value": proxy}, {"label": "Active / narrative-resolved conflicts retained", "value": conflicts}, {"label": "Conditional load if all such conflicts were resolved", "value": f"{n-conflicts}–{n}" if n is not None else None}, {"label": "Historical daily backlog", "value": "Unavailable; no synthetic series"}]
    r["method"] = "Count all operationally active or unknown-status guest issues across source dates, including carry-ins. Consolidate only exact saved dedupe identities or matching property/reservation/complaint keys; possible text duplicates are not merged. Narrative-resolved conflicts remain active. The sensitivity range conditionally removes those conflicts and is not a confidence interval. Current catalog entries proxy for units; archive/scope and bundle mapping are not a physical-unit register."
    exception(r, "Reconcile active/narrative-resolved conflicts and confirm the physical-unit count before certifying the target.")
    r["observation"] = f"{proxy} catalog entries as unit proxy · physical count unverified" if proxy else None
    return r


CLEANING_STRICT = {"cleanliness", "housekeeping", "deep_cleaning", "cleaning_readiness", "cleanliness_turnover", "turnover_readiness", "linen_cleanliness", "dishware_cleanliness", "kitchen_cleanliness", "appliance_cleanliness", "pool_cleanliness", "hot_tub_cleanliness", "outdoor_cleanliness", "exterior_cleanliness", "yard_cleanliness", "outdoor_amenity_cleanliness"}
CLEANING_AMBIGUOUS = {"linens", "linens_and_bedding", "linen_condition", "missing_linens", "laundry", "turnover_laundry", "laundry_readiness", "odor", "odor_smoke", "odor_mildew", "pests", "pest_infestation", "trash", "waste_management", "cleaning_supplies", "accuracy_and_cleanliness_signal"}


def estimate_cleaning(data, windows):
    r = estimated("cleaning", "estimated turnover incidents per 10 booked check-ins")
    checkins = {x["id"]: x for x in data.get("reservations", []) if qualifying(x) and windows.get(x["listing_id"]) and windows[x["listing_id"]]["start"] <= day(x["arrival"]) < windows[x["listing_id"]]["end"]}
    strict, possible, unmatched = set(), set(), 0
    distinct = set()
    # Includes resolved and active issues; this is an incidence metric, not backlog.
    for x in data.get("issues", []):
        category = str(x.get("category") or "").lower()
        if category not in CLEANING_STRICT | CLEANING_AMBIGUOUS: continue
        reservation = checkins.get(x.get("reservation_id"))
        if not reservation or reservation["listing_id"] != x["listing_id"]:
            w = windows.get(x["listing_id"])
            if w and x.get("date") and w["start"] <= day(x["date"]) < w["end"]: unmatched += 1
            continue
        possible.add(x["reservation_id"]); distinct.add(issue_identity(x))
        if category in CLEANING_STRICT: strict.add(x["reservation_id"])
    if checkins and data.get("issues_readable") and (possible or not unmatched):
        value = ratio(len(strict), len(checkins), 10)
        r.update(value=value, display=f"{value:g}", numerator=len(strict), denominator=len(checkins))
    high = ratio(len(distinct), len(checkins), 10)
    for lid in windows:
        ids = {key for key, x in checkins.items() if x["listing_id"] == lid}
        if ids:
            r["breakdown"].append({"group": str(lid), "estimated_incidents": len(strict & ids), "possible_affected_checkins": len(possible & ids), "booked_checkins": len(ids), "estimated_per_10": ratio(len(strict & ids), len(ids), 10)})
    r["breakdown"].sort(key=lambda x: -x["estimated_per_10"])
    weeks = defaultdict(lambda: [set(), set()])
    for key, x in checkins.items():
        d = day(x["arrival"]); bucket = d - timedelta(days=d.weekday())
        weeks[bucket][1].add(key)
        if key in strict: weeks[bucket][0].add(key)
    if data.get("issues_readable"):
        r["trend"] = [{"date": d.isoformat(), "value": ratio(len(a), len(b), 10), "checkins": len(b), "target": 1} for d, (a, b) in sorted(weeks.items())]
    r.update(numerator_label="Booked check-ins with explicitly cleaning-tagged issues, active or resolved", denominator_label="Observed qualifying booked check-ins; actual arrival and package identity unverified", summary="Turnover estimate groups cleaning complaints per check-in; ambiguous categories form an upper sensitivity scenario.")
    r["diagnostics"] = [{"label": "Conditional incident-rate sensitivity per 10", "value": f"{r['value']:g}–{high:g}" if r["value"] is not None and high is not None else None}, {"label": "Strict / possible affected check-ins", "value": f"{len(strict)} / {len(possible)}"}, {"label": "In-window candidates not joined to eligible check-ins", "value": unmatched}, {"label": "Issue statuses included", "value": "Active and resolved"}]
    r["method"] = "Explicit cleaning/housekeeping/cleanliness category names map to a turnover incident estimate, consolidating complaints per qualifying booked reservation. Linens, laundry, odors, pests and waste tags are ambiguous. The upper sensitivity scenario allows separate saved complaint identities and ambiguous categories; it is not a confidence interval or an exhaustive bound. Active and resolved records are included. Candidates lacking an eligible reservation/listing join are excluded and reported. Booked check-ins proxy for actual arrivals; package duplicates and classification coverage remain unverified."
    exception(r, "Review ambiguous categories and unmatched check-in joins; validate distinct incidents and actual arrivals before certification.")
    r["observation"] = f"{len(strict)} estimated affected check-ins / {len(checkins)} booked check-ins" if checkins else "No usable check-in denominator"
    return r


def apply_estimates(metrics, data, windows, review_windows, as_of):
    replacements = {"revenue": lambda: estimate_revenue(data, windows), "reviews": lambda: estimate_reviews(data, review_windows), "response": lambda: estimate_response(data, windows, as_of), "issues": lambda: estimate_issues(data), "cleaning": lambda: estimate_cleaning(data, windows)}
    result = []
    for m in metrics:
        if m["key"] == "content":
            m = metric("content")
            m.update(display="Deferred", data_status="deferred", goal_status="deferred", target="Team definition pending", summary="Listing refresh and edit-history tracking are deferred for team discussion.", observation=None)
        elif m["data_status"] != "verified": m = replacements[m["key"]]()
        result.append(m)
    return result
