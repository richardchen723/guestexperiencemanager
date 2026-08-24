#!/usr/bin/env python3
"""Bounded extraction and comparison for public listing channel pages."""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import defaultdict
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse


CHANNEL_HOST_SUFFIXES = {
    "airbnb": ("airbnb.com",),
    "vrbo": ("vrbo.com", "homeaway.com"),
    "bookingcom": ("booking.com",),
    "googlevr": ("google.com",),
}

MAX_VISIBLE_TEXT = 50_000
MAX_SCRIPT_TEXT = 2_000_000
MAX_JSON_NODES = 25_000
MAX_FIELD_TEXT = 1_500
MAX_AMENITIES = 60
DEEP_BROWSER_WORKERS = max(1, min(int(os.getenv("LISTING_AUDIT_DEEP_BROWSER_WORKERS", "4")), 8))
DEEP_BROWSER_TIMEOUT_MS = max(5_000, min(int(os.getenv("LISTING_AUDIT_DEEP_BROWSER_TIMEOUT_MS", "18000")), 45_000))
DEEP_BROWSER_SETTLE_MS = max(500, min(int(os.getenv("LISTING_AUDIT_DEEP_BROWSER_SETTLE_MS", "1500")), 5_000))

_SPACE_PATTERN = re.compile(r"\s+")
_WORD_PATTERN = re.compile(r"[a-z0-9]+")
_PLACEHOLDER_PATTERN = re.compile(
    r"\b(?:lorem ipsum|todo|tbd|coming soon|test listing|sample text|xxx+)\b",
    re.IGNORECASE,
)
_RENDERED_PAGE_ERROR_MARKERS = (
    ("oops, something went wrong",),
    ("having trouble loading details",),
    ("we couldn't find the page",),
    ("we can't find the page",),
    ("this page isn't available",),
    ("this page is no longer available",),
)
_AUTOMATION_BLOCK_MARKERS = (
    "verify you are human",
    "captcha",
    "access denied",
    "robot check",
    "are you a robot",
    "bot or not",
)
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is",
    "it", "of", "on", "or", "our", "the", "this", "to", "with", "your",
}

_STRUCTURED_FIELD_KEYS = {
    "title": {"title", "name", "headline"},
    "description": {"description", "summary", "about"},
    "location": {
        "address", "location", "addresslocality", "addressregion", "addresscountry",
        "streetaddress", "postalcode",
    },
    "amenities": {"amenities", "amenityfeature", "facilityfeature", "features"},
    "guest_notes": {"guestnotes", "notes", "otherthingstonote", "thingstoknow"},
    "house_rules": {"houserules", "houserule", "rules", "policies"},
}

_SECTION_MARKERS = {
    "location": ("where you’ll be", "where you'll be", "location", "neighborhood"),
    "amenities": ("what this place offers", "amenities", "property amenities"),
    "guest_notes": ("other things to note", "guest notes", "things to know"),
    "house_rules": ("house rules", "property rules", "rules and policies"),
}


def clean_text(value: Any, *, limit: int = MAX_FIELD_TEXT) -> str:
    text = _SPACE_PATTERN.sub(" ", str(value or "")).strip()
    return text[:limit]


def rendered_page_error_message(value: Any) -> str:
    """Return visible channel error text while ignoring sparse-but-working pages."""
    text = clean_text(value, limit=8_000)
    lower = text.casefold()
    if any(all(marker in lower for marker in markers) for markers in _RENDERED_PAGE_ERROR_MARKERS):
        return text[:280]
    return ""


def automation_blocked_page_message(value: Any) -> str:
    """Return automation-block copy that must not be reported as a guest-page failure."""
    text = clean_text(value, limit=8_000)
    lower = text.casefold()
    if any(marker in lower for marker in _AUTOMATION_BLOCK_MARKERS):
        return text[:280]
    return ""


def channel_destination_valid(url: str | None, channel: str) -> bool:
    """Return whether the final guest URL remains on the expected channel domain."""
    if not url:
        return False
    suffixes = CHANNEL_HOST_SUFFIXES.get(channel)
    if not suffixes:
        return True
    try:
        hostname = (urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in suffixes)


class _PublicPageParser(HTMLParser):
    """Collect visible text and bounded JSON scripts without retaining raw HTML."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.visible_parts: list[str] = []
        self.title_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.json_scripts: list[str] = []
        self.image_count = 0
        self._ignored_depth = 0
        self._in_title = False
        self._json_script = False
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]):
        tag = tag.lower()
        attr_map = {str(key).lower(): value for key, value in attrs}
        if tag in {"style", "noscript", "svg"}:
            self._ignored_depth += 1
        if tag == "script":
            script_type = str(attr_map.get("type") or "").lower()
            script_id = str(attr_map.get("id") or "").lower()
            self._json_script = "json" in script_type or script_id in {"__next_data__", "data-deferred-state"}
            self._script_parts = []
            self._ignored_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            key = str(attr_map.get("property") or attr_map.get("name") or "").lower()
            content = clean_text(attr_map.get("content"), limit=2_000)
            if key and content:
                self.meta.setdefault(key, content)
        elif tag == "img":
            self.image_count += 1

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag == "script":
            if self._json_script:
                script = "".join(self._script_parts).strip()
                if script and len(script) <= MAX_SCRIPT_TEXT:
                    self.json_scripts.append(script)
            self._json_script = False
            self._script_parts = []
            self._ignored_depth = max(0, self._ignored_depth - 1)
        elif tag in {"style", "noscript", "svg"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str):
        if self._json_script:
            if sum(len(part) for part in self._script_parts) < MAX_SCRIPT_TEXT:
                self._script_parts.append(data)
            return
        text = clean_text(data, limit=10_000)
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        if self._ignored_depth == 0 and sum(len(part) for part in self.visible_parts) < MAX_VISIBLE_TEXT:
            self.visible_parts.append(text)


def extract_deep_page_content(html_text: str, *, fallback_title: str = "", fallback_description: str = "") -> dict[str, Any]:
    """Extract bounded public content signals used by the weekly deep audit."""
    parser = _PublicPageParser()
    try:
        parser.feed((html_text or "")[:8_000_000])
    except Exception:
        pass

    visible_text = clean_text(" ".join(parser.visible_parts), limit=MAX_VISIBLE_TEXT)
    structured = _structured_signals(parser.json_scripts)
    title = (
        parser.meta.get("og:title")
        or parser.meta.get("twitter:title")
        or clean_text(" ".join(parser.title_parts), limit=500)
        or fallback_title
        or _best_text(structured["title"], prefer_long=False)
    )
    description = (
        parser.meta.get("og:description")
        or parser.meta.get("description")
        or fallback_description
        or _best_text(structured["description"])
    )
    fields = {
        "title": clean_text(title, limit=500),
        "description": clean_text(description),
        "location": _best_text(structured["location"]) or _visible_section(visible_text, "location"),
        "amenities": _amenity_values(structured["amenities"]),
        "guest_notes": _best_text(structured["guest_notes"]) or _visible_section(visible_text, "guest_notes"),
        "house_rules": _best_text(structured["house_rules"]) or _visible_section(visible_text, "house_rules"),
    }
    structured_search = " ".join(
        _flatten_value(value, limit=2_000)
        for values in structured.values()
        for value in values[:200]
    )
    return {
        "fields": fields,
        "visible_text_length": len(visible_text),
        "structured_data_blocks": len(parser.json_scripts),
        "page_image_count": parser.image_count,
        "_search_text": clean_text(f"{visible_text} {structured_search}", limit=MAX_VISIBLE_TEXT),
    }


def deep_content_is_sparse(page: dict[str, Any]) -> bool:
    if page.get("status") != "ok":
        return False
    deep_content = page.get("deep_content") or {}
    fields = deep_content.get("fields") or {}
    present = sum(bool(fields.get(field)) for field in ("title", "description", "location", "amenities", "guest_notes", "house_rules"))
    return present < 3


def render_deep_public_pages(targets: dict[Any, tuple[str, str]]) -> dict[Any, dict[str, Any]]:
    """Render sparse JavaScript pages with one bounded headless Chromium pool."""
    if not targets:
        return {}

    async def run() -> dict[Any, dict[str, Any]]:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return {
                key: {"status": "unavailable", "error": "Playwright is not installed."}
                for key in targets
            }

        results: dict[Any, dict[str, Any]] = {}
        semaphore = asyncio.Semaphore(DEEP_BROWSER_WORKERS)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124 Safari/537.36",
                locale="en-US",
                java_script_enabled=True,
            )

            async def inspect(key: Any, url: str, channel: str):
                async with semaphore:
                    checked_at = _utc_now()
                    page = await context.new_page()
                    try:
                        response = await page.goto(url, wait_until="domcontentloaded", timeout=DEEP_BROWSER_TIMEOUT_MS)
                        await page.wait_for_timeout(DEEP_BROWSER_SETTLE_MS)
                        final_url = page.url or url
                        html_text = await page.content()
                        title = clean_text(await page.title(), limit=500)
                        try:
                            body_text = await page.locator("body").inner_text(timeout=3_000)
                        except Exception:
                            body_text = title
                        lower = clean_text(body_text, limit=8_000).lower()
                        rendered_error = rendered_page_error_message(body_text)
                        automation_block = automation_blocked_page_message(body_text)
                        http_status = response.status if response else None
                        domain_valid = channel_destination_valid(final_url, channel)
                        failure_kind = None
                        if http_status in {404, 410} or "page not found" in lower:
                            status = "not_found"
                            failure_kind = "not_found"
                        elif not domain_valid:
                            status = "invalid_domain"
                            failure_kind = "invalid_domain"
                        elif automation_block:
                            status = "blocked"
                            failure_kind = "automation_blocked"
                        elif http_status and http_status >= 400:
                            status = "unavailable"
                            failure_kind = "http_error"
                        elif rendered_error:
                            status = "unavailable"
                            failure_kind = "rendered_error"
                        else:
                            status = "ok"
                        result = {
                            "status": status,
                            "url": final_url,
                            "requested_url": url,
                            "checked_at": checked_at,
                            "http_status": http_status,
                            "content_type": "text/html",
                            "domain_valid": domain_valid,
                            "redirected": final_url.rstrip("/") != url.rstrip("/"),
                            "inspection_mode": "deep",
                            "browser_rendered": True,
                            "title": title,
                            "summary": rendered_error or title or "Rendered public page returned without extractable text.",
                        }
                        if failure_kind:
                            result["failure_kind"] = failure_kind
                        if status == "ok":
                            content = extract_deep_page_content(html_text, fallback_title=title)
                            result["_deep_search_text"] = content.pop("_search_text", "")
                            result["deep_content"] = content
                            result["meta_description"] = content.get("fields", {}).get("description") or ""
                        results[key] = result
                    except Exception as exc:
                        results[key] = {
                            "status": "unavailable",
                            "url": url,
                            "requested_url": url,
                            "checked_at": checked_at,
                            "domain_valid": channel_destination_valid(url, channel),
                            "redirected": False,
                            "inspection_mode": "deep",
                            "browser_rendered": True,
                            "failure_kind": "request_error",
                            "error": str(exc)[:500],
                        }
                    finally:
                        await page.close()

            await asyncio.gather(*(
                inspect(key, url, channel)
                for key, (url, channel) in targets.items()
            ))
            await context.close()
            await browser.close()
        return results

    try:
        return asyncio.run(run())
    except Exception as exc:
        return {
            key: {"status": "unavailable", "error": str(exc)[:500], "browser_rendered": True}
            for key in targets
        }


def build_deep_channel_inspection(
    *,
    detail: dict[str, Any],
    channel: str,
    label: str,
    page: dict[str, Any],
    source_title: str,
    source_description: str,
) -> dict[str, Any]:
    """Compare a deep public-page extraction with Hostaway's source content."""
    deep_content = dict(page.get("deep_content") or {})
    observed = dict(deep_content.get("fields") or {})
    search_text = clean_text(page.get("_deep_search_text") or deep_content.pop("_search_text", ""), limit=MAX_VISIBLE_TEXT)
    source = _source_fields(detail, channel, source_title, source_description)
    fields: dict[str, dict[str, Any]] = {}
    issues: list[dict[str, str]] = []
    seen_codes: set[str] = set()

    def issue(priority: str, field: str, code: str, message: str):
        if code in seen_codes:
            return
        seen_codes.add(code)
        issues.append({"priority": priority, "field": field, "code": code, "message": message})

    page_status = str(page.get("status") or "unavailable")
    if page_status == "missing_url":
        issue("high", "link", "missing_public_url", f"Store the public {label} URL so the guest page can be validated and inspected.")
    elif page_status in {"not_found", "invalid_domain"}:
        issue("critical", "link", f"public_page_{page_status}", f"Repair the {label} guest URL; it did not resolve to a valid live {label} page.")
    elif page_status in {"blocked", "unavailable", "non_html"}:
        issue("high", "link", f"public_page_{page_status}", f"Manually inspect the {label} guest page; the deep audit could not read its public content.")

    deep_field_count = sum(bool(observed.get(field)) for field in ("title", "description", "location", "amenities", "guest_notes", "house_rules"))
    deep_content = page.get("deep_content") or {}
    page_accessible = page_status == "ok" and (
        int(deep_content.get("visible_text_length") or 0) >= 500
        or int(deep_content.get("structured_data_blocks") or 0) > 0
        or bool(observed.get("title"))
    )
    deep_content_complete = deep_field_count >= 3
    for field in ("title", "description", "location", "amenities", "guest_notes", "house_rules"):
        expected_value = source.get(field)
        observed_value = observed.get(field)
        expected_present = bool(expected_value)
        observed_present = bool(observed_value)
        review = {
            "label": field.replace("_", " ").title(),
            "status": "unverified",
            "source_present": expected_present,
            "page_present": observed_present,
            "source_excerpt": _field_excerpt(expected_value),
            "page_excerpt": _field_excerpt(observed_value),
        }

        if field == "guest_notes" and channel != "airbnb" and not expected_present:
            review["status"] = "not_applicable"
        elif not expected_present:
            review["status"] = "source_missing"
            priority = "medium" if field in {"title", "description", "location", "amenities", "house_rules"} else "low"
            issue(priority, field, f"source_missing_{field}", f"Add {review['label'].lower()} to the Hostaway source content used by {label}.")
        elif not page_accessible:
            review["status"] = "unverified"
        elif not observed_present:
            search_match = _token_coverage(expected_value, search_text)
            if search_match is not None and search_match >= _match_threshold(field):
                review["status"] = "match"
                review["match_score"] = search_match
                review["page_present"] = True
            elif deep_content_complete:
                review["status"] = "not_found_on_page"
                priority = "high" if field == "title" else "medium" if field in {"description", "location", "amenities"} else "low"
                issue(priority, field, f"page_missing_{field}", f"Verify {review['label'].lower()} on {label}; it was not found in the public page source.")
            else:
                review["status"] = "unverified"
        else:
            match_score = _field_match_score(field, expected_value, observed_value, search_text)
            review["match_score"] = match_score
            if match_score is None:
                review["status"] = "present"
            elif match_score >= _match_threshold(field):
                review["status"] = "match"
            elif field == "amenities" and match_score > 0:
                review["status"] = "partial"
                issue("medium", field, "page_partial_amenities", f"Review {label} amenities; only part of the Hostaway amenity set was confirmed in the public page source.")
            elif field == "location":
                review["status"] = "partial"
                issue("medium", field, "page_location_unconfirmed", f"Manually confirm the {label} location; the public page uses broader or different location wording than Hostaway.")
            else:
                review["status"] = "mismatch"
                priority = "high" if field in {"title", "location"} else "medium"
                issue(priority, field, f"page_mismatch_{field}", f"Reconcile the {label} {review['label'].lower()} with Hostaway; the public content appears inconsistent.")
        fields[field] = review

    verified_count = sum(1 for item in fields.values() if item["status"] in {"match", "present"})
    if page_status == "ok" and verified_count < 4:
        issue(
            "high",
            "page",
            "deep_content_unverified",
            f"The {label} link is live, but fewer than four detailed content areas could be verified from its dynamic guest page.",
        )
    elif page_status == "ok":
        required_fields = ["title", "description", "location", "amenities", "house_rules"]
        if channel == "airbnb":
            required_fields.append("guest_notes")
        unverified_labels = [
            fields[field]["label"].lower()
            for field in required_fields
            if fields[field]["status"] == "unverified"
        ]
        if unverified_labels:
            issue(
                "medium",
                "page",
                "deep_fields_unverified",
                f"Manually verify {', '.join(unverified_labels)} on {label}; those sections could not be confirmed from the rendered guest page.",
            )

    source_title_text = clean_text(source.get("title"), limit=500)
    source_description_text = clean_text(source.get("description"))
    if source_title_text and len(source_title_text) < 24:
        issue("medium", "title", "title_too_short", f"Strengthen the {label} title; it is only {len(source_title_text)} characters.")
    if source_description_text and len(source_description_text) < 220:
        issue("medium", "description", "description_too_short", f"Expand the {label} description; it is only {len(source_description_text)} characters.")
    source_amenities = source.get("amenities") or []
    if source_amenities and len(source_amenities) < 10:
        issue("medium", "amenities", "amenities_too_few", f"Review {label} amenity coverage; Hostaway provides only {len(source_amenities)} named amenities.")
    for field in ("title", "description", "guest_notes", "house_rules"):
        combined = f"{_field_excerpt(source.get(field))} {_field_excerpt(observed.get(field))}"
        if _PLACEHOLDER_PATTERN.search(combined):
            issue("high", field, f"placeholder_{field}", f"Remove placeholder or test wording from the {label} {field.replace('_', ' ')}.")

    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    issues.sort(key=lambda item: (priority_order.get(item["priority"], 9), item["field"], item["code"]))
    counts = {priority: sum(1 for item in issues if item["priority"] == priority) for priority in priority_order}
    if counts["critical"]:
        status = "critical"
    elif counts["high"]:
        status = "high"
    elif counts["medium"] or counts["low"]:
        status = "watch"
    else:
        status = "healthy"
    return {
        "status": status,
        "reviewed_at": page.get("checked_at"),
        "page_status": page_status,
        "domain_valid": bool(page.get("domain_valid")),
        "redirected": bool(page.get("redirected")),
        "fields": fields,
        "issues": issues[:18],
        "issue_counts": counts,
        "verified_field_count": verified_count,
        "field_count": len(fields),
        "summary": (
            f"{verified_count} of {len(fields)} fields verified; "
            f"{len(issues)} finding{'s' if len(issues) != 1 else ''}."
        ),
    }


def _structured_signals(scripts: list[str]) -> dict[str, list[Any]]:
    result: dict[str, list[Any]] = defaultdict(list)
    nodes_seen = 0

    def visit(value: Any, depth: int = 0):
        nonlocal nodes_seen
        if nodes_seen >= MAX_JSON_NODES or depth > 14:
            return
        nodes_seen += 1
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
                for field, keys in _STRUCTURED_FIELD_KEYS.items():
                    if normalized in keys:
                        result[field].append(nested)
                visit(nested, depth + 1)
        elif isinstance(value, list):
            for nested in value[:500]:
                visit(nested, depth + 1)

    for script in scripts[:40]:
        try:
            visit(json.loads(script))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return result


def _flatten_value(value: Any, *, limit: int = MAX_FIELD_TEXT) -> str:
    if isinstance(value, str):
        return clean_text(value, limit=limit)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, dict):
        preferred = []
        for key in ("name", "value", "description", "streetAddress", "addressLocality", "addressRegion", "addressCountry"):
            if key in value:
                preferred.append(_flatten_value(value.get(key), limit=limit))
        values = preferred or [_flatten_value(item, limit=limit) for item in list(value.values())[:30]]
        return clean_text(" · ".join(item for item in values if item), limit=limit)
    if isinstance(value, list):
        return clean_text(" · ".join(_flatten_value(item, limit=300) for item in value[:60]), limit=limit)
    return ""


def _best_text(values: list[Any], *, prefer_long: bool = True) -> str:
    candidates = [_flatten_value(value) for value in values]
    candidates = [value for value in candidates if value]
    if not candidates:
        return ""
    return max(candidates, key=len) if prefer_long else candidates[0]


def _amenity_values(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        items = value if isinstance(value, list) else [value]
        for item in items:
            text = _flatten_value(item, limit=300)
            for candidate in re.split(r"\s*[|;•·]\s*", text):
                candidate = clean_text(candidate, limit=160)
                key = candidate.casefold()
                if candidate and key not in seen:
                    seen.add(key)
                    result.append(candidate)
                    if len(result) >= MAX_AMENITIES:
                        return result
    return result


def _visible_section(visible_text: str, field: str) -> str:
    lower = visible_text.lower()
    starts = []
    for marker in _SECTION_MARKERS.get(field, ()):
        index = lower.find(marker)
        if index >= 0:
            starts.append((index, marker))
    if not starts:
        return ""
    start, marker = min(starts)
    content_start = start + len(marker)
    end = min(len(visible_text), content_start + MAX_FIELD_TEXT)
    for markers in _SECTION_MARKERS.values():
        for other in markers:
            index = lower.find(other, content_start + 20)
            if 0 <= index < end:
                end = index
    return clean_text(visible_text[content_start:end])


def _listing_amenities(detail: dict[str, Any]) -> list[str]:
    values = detail.get("listingAmenities") or detail.get("amenities") or []
    if isinstance(values, dict):
        values = list(values.values())
    if not isinstance(values, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        if isinstance(item, dict):
            value = item.get("name") or item.get("amenityName") or item.get("title") or item.get("value")
        else:
            value = item
        text = clean_text(value, limit=160)
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
    return result[:MAX_AMENITIES]


def _source_fields(detail: dict[str, Any], channel: str, title: str, description: str) -> dict[str, Any]:
    location = clean_text(" · ".join(
        str(value).strip()
        for value in (detail.get("city"), detail.get("state"), detail.get("country"))
        if str(value or "").strip()
    ), limit=500)
    guest_notes = detail.get("airbnbNotes") if channel == "airbnb" else None
    return {
        "title": clean_text(title, limit=500),
        "description": clean_text(description),
        "location": location,
        "amenities": _listing_amenities(detail),
        "guest_notes": clean_text(guest_notes),
        "house_rules": clean_text(detail.get("houseRules")),
    }


def _field_excerpt(value: Any) -> Any:
    if isinstance(value, list):
        return [clean_text(item, limit=160) for item in value[:20] if clean_text(item, limit=160)]
    return clean_text(value, limit=600)


def _tokens(value: Any) -> set[str]:
    if isinstance(value, list):
        text = " ".join(str(item) for item in value)
    else:
        text = str(value or "")
    return {token for token in _WORD_PATTERN.findall(text.lower()) if len(token) > 2 and token not in _STOP_WORDS}


def _field_match_score(field: str, expected: Any, observed: Any, search_text: str) -> float | None:
    expected_tokens = _tokens(expected)
    observed_tokens = _tokens(observed)
    observed_tokens |= _tokens(search_text)
    if not expected_tokens or not observed_tokens:
        return None
    if field == "location":
        city_tokens = _tokens(str(expected or "").split("·", 1)[0])
        if city_tokens and city_tokens.issubset(observed_tokens):
            return 1.0
    return round(len(expected_tokens & observed_tokens) / len(expected_tokens), 2)


def _token_coverage(expected: Any, observed: Any) -> float | None:
    expected_tokens = _tokens(expected)
    observed_tokens = _tokens(observed)
    if not expected_tokens or not observed_tokens:
        return None
    return round(len(expected_tokens & observed_tokens) / len(expected_tokens), 2)


def _match_threshold(field: str) -> float:
    return {
        "title": 0.45,
        "description": 0.15,
        "location": 0.3,
        "amenities": 0.15,
        "guest_notes": 0.2,
        "house_rules": 0.2,
    }.get(field, 0.3)


def _utc_now() -> str:
    from datetime import datetime

    return datetime.utcnow().isoformat()
