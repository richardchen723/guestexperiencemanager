#!/usr/bin/env python3
"""
Vision-based Airbnb browser automation engine.

Uses screenshots + GPT-4o vision to decide what to click, and Playwright raw
mouse/keyboard IO to interact.  Zero CSS selectors, zero DOM queries -- the
page is a black box of pixels, exactly like a real human sees it.
"""

import asyncio
import logging
import os
import random
import re
import threading
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import dashboard.config as config
from dashboard.boost.human_behavior import (
    get_random_locale,
    get_random_timezone,
    get_random_user_agent,
    get_random_viewport,
    human_click,
    human_press_key,
    human_scroll,
    human_type,
    map_drag,
    map_zoom,
    mouse_wander,
    random_delay,
    reading_pause,
    smooth_mouse_move,
)
from dashboard.boost.vision import (
    analyze_screen,
    goal_click_next_page,
    goal_click_random_listing,
    goal_dismiss_popup,
    goal_engage_listing,
    goal_scan_results,
)

logger = logging.getLogger(__name__)

MAX_PAGES_TO_BROWSE = 15
MAP_LISTING_COUNT_MIN = 150
MAP_LISTING_COUNT_MAX = 450
DEBUG_SCREENSHOTS_DIR = config.BOOST_RECORDINGS_DIR
BLOCKING_DIALOG_SIGNATURES = (
    (
        "translation dialog",
        (
            "translation on",
            "translation settings",
            "automatically translated",
        ),
    ),
    (
        "login dialog",
        (
            "log in or sign up",
            "welcome to airbnb",
            "continue with google",
            "continue with apple",
            "continue with email",
            "continue with facebook",
            "phone number",
            "country code",
        ),
    ),
)
MODAL_CANDIDATE_SELECTOR = '[role="dialog"], [aria-modal="true"], div[data-testid="modal-container"]'


class SessionCancelled(Exception):
    """Raised when a session is terminated by the user."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pick_search_dates(
    window_start: date,
    window_end: date,
    min_nights: int,
    max_nights: int,
    available_windows: Optional[List[Tuple[date, date]]] = None,
) -> Tuple[date, date]:
    """Pick random check-in / check-out dates within the campaign window.

    If *available_windows* is provided (from the Hostaway calendar), the
    selected dates will fall entirely within an available window so that
    the listing actually appears in Airbnb search results.
    """
    nights = random.randint(min_nights, max_nights)

    if available_windows:
        valid = [
            (ws, we) for ws, we in available_windows
            if (we - ws).days + 1 >= nights
        ]
        if valid:
            ws, we = random.choice(valid)
            latest_ci = we - timedelta(days=nights - 1)
            delta = (latest_ci - ws).days
            checkin = ws + timedelta(days=random.randint(0, max(0, delta)))
            checkout = checkin + timedelta(days=nights)
            return checkin, checkout
        logger.warning("No available windows long enough; falling back to "
                       "campaign window (listing may not appear in results)")

    latest_checkin = window_end - timedelta(days=nights)
    if latest_checkin < window_start:
        latest_checkin = window_start
    delta = (latest_checkin - window_start).days
    checkin = window_start + timedelta(days=random.randint(0, max(0, delta)))
    checkout = checkin + timedelta(days=nights)
    return checkin, checkout


def _extract_listing_id(url: str) -> Optional[str]:
    match = re.search(r"/rooms/(\d+)", url)
    return match.group(1) if match else None


def _match_dialog_signature(text: str) -> Optional[str]:
    """Return the known dialog label for the given text, if any."""
    for label, markers in BLOCKING_DIALOG_SIGNATURES:
        if any(marker in text for marker in markers):
            return label
    return None


async def _clear_page_storage(page, log=None):
    """Best-effort cleanup for page-scoped storage and caches."""
    try:
        await page.evaluate(
            """
            async () => {
              try { window.localStorage.clear(); } catch (e) {}
              try { window.sessionStorage.clear(); } catch (e) {}

              try {
                if ('indexedDB' in window) {
                  if (typeof indexedDB.databases === 'function') {
                    const dbs = await indexedDB.databases();
                    await Promise.all(
                      (dbs || [])
                        .map((db) => db && db.name)
                        .filter(Boolean)
                        .map((name) => new Promise((resolve) => {
                          try {
                            const req = indexedDB.deleteDatabase(name);
                            req.onsuccess = () => resolve(true);
                            req.onerror = () => resolve(false);
                            req.onblocked = () => resolve(false);
                          } catch (err) {
                            resolve(false);
                          }
                        }))
                    );
                  }
                }
              } catch (e) {}

              try {
                if ('caches' in window) {
                  const keys = await caches.keys();
                  await Promise.all(keys.map((key) => caches.delete(key)));
                }
              } catch (e) {}

              try {
                if ('serviceWorker' in navigator && navigator.serviceWorker.getRegistrations) {
                  const regs = await navigator.serviceWorker.getRegistrations();
                  await Promise.all(regs.map((reg) => reg.unregister().catch(() => false)));
                }
              } catch (e) {}

              return true;
            }
            """
        )
        if log:
            log("Cleared page storage for a fresh browsing surface")
    except Exception as exc:
        if log:
            log(f"Could not fully clear page storage: {exc}")


async def _modal_candidate_snapshots(page, limit: int = 8) -> List[Dict[str, Any]]:
    """Collect lightweight DOM facts about visible modal-like candidates."""
    try:
        snapshots = await page.evaluate(
            """
            ({ selector, limit }) => {
              const vw = window.innerWidth || document.documentElement.clientWidth || 0;
              const vh = window.innerHeight || document.documentElement.clientHeight || 0;
              const centerX = Math.floor(vw / 2);
              const centerY = Math.floor(vh / 2);
              const centerEl = document.elementFromPoint(centerX, centerY);

              return Array.from(document.querySelectorAll(selector))
                .slice(0, limit)
                .map((el, idx) => {
                  const rect = el.getBoundingClientRect();
                  const style = window.getComputedStyle(el);
                  const opacity = Number.parseFloat(style.opacity || "1") || 0;
                  const visible =
                    rect.width > 1 &&
                    rect.height > 1 &&
                    rect.bottom > 0 &&
                    rect.right > 0 &&
                    rect.left < vw &&
                    rect.top < vh &&
                    style.display !== "none" &&
                    style.visibility !== "hidden" &&
                    opacity > 0.05;

                  const text = ((el.innerText || el.textContent || "") + "")
                    .replace(/\\s+/g, " ")
                    .trim()
                    .toLowerCase();

                  const buttons = Array.from(el.querySelectorAll("button, [role='button']")).slice(0, 20);
                  const buttonFacts = buttons.map((btn) => {
                    const r = btn.getBoundingClientRect();
                    const label = (
                      btn.getAttribute("aria-label") ||
                      btn.getAttribute("title") ||
                      btn.innerText ||
                      btn.textContent ||
                      ""
                    )
                      .replace(/\\s+/g, " ")
                      .trim()
                      .toLowerCase();
                    const centerBtnX = r.x + (r.width / 2);
                    const centerBtnY = r.y + (r.height / 2);
                    const isCorner =
                      r.width > 0 &&
                      r.height > 0 &&
                      centerBtnY <= rect.y + (rect.height * 0.22) &&
                      (
                        centerBtnX <= rect.x + (rect.width * 0.28) ||
                        centerBtnX >= rect.x + (rect.width * 0.72)
                      );
                    return {
                      label,
                      visible: r.width > 0 && r.height > 0,
                      isCorner,
                      isLabeledClose: /close|dismiss|cancel/.test(label),
                    };
                  });

                  return {
                    idx,
                    visible,
                    text,
                    role: (el.getAttribute("role") || "").toLowerCase(),
                    ariaModal: (el.getAttribute("aria-modal") || "").toLowerCase(),
                    testId: (el.getAttribute("data-testid") || "").toLowerCase(),
                    rect: {
                      x: rect.x,
                      y: rect.y,
                      width: rect.width,
                      height: rect.height,
                    },
                    areaRatio: vw && vh ? (rect.width * rect.height) / (vw * vh) : 0,
                    position: (style.position || "").toLowerCase(),
                    zIndex: Number.parseFloat(style.zIndex || "0") || 0,
                    pointerEvents: (style.pointerEvents || "").toLowerCase(),
                    ownsCenter: !!centerEl && el.contains(centerEl),
                    closeButtonCount: buttonFacts.filter((b) => b.visible && b.isLabeledClose).length,
                    cornerButtonCount: buttonFacts.filter((b) => b.visible && b.isCorner).length,
                    scrollHeight: el.scrollHeight || 0,
                    clientHeight: el.clientHeight || 0,
                    viewport: { width: vw, height: vh },
                  };
                })
                .filter((item) => item.visible);
            }
            """,
            {"selector": MODAL_CANDIDATE_SELECTOR, "limit": limit},
        )
    except Exception:
        return []

    return snapshots or []


def _looks_like_blocking_overlay(snapshot: Dict[str, Any]) -> bool:
    """Heuristic for whether a DOM candidate is a real screen-blocking overlay."""
    rect = snapshot.get("rect") or {}
    area_ratio = float(snapshot.get("areaRatio") or 0.0)
    width = float(rect.get("width") or 0.0)
    height = float(rect.get("height") or 0.0)
    viewport = snapshot.get("viewport") or {}
    viewport_width = float(viewport.get("width") or 0.0)
    viewport_height = float(viewport.get("height") or 0.0)
    position = (snapshot.get("position") or "").lower()
    role = (snapshot.get("role") or "").lower()
    aria_modal = (snapshot.get("ariaModal") or "").lower()
    test_id = (snapshot.get("testId") or "").lower()

    if snapshot.get("pointerEvents") == "none":
        return False
    if not snapshot.get("ownsCenter"):
        return False
    if width < max(280.0, viewport_width * 0.28):
        return False
    if height < max(180.0, viewport_height * 0.18):
        return False
    if area_ratio < 0.08:
        return False

    overlay_shell = (
        position in {"fixed", "absolute", "sticky"}
        or role == "dialog"
        or aria_modal == "true"
        or test_id == "modal-container"
        or float(snapshot.get("zIndex") or 0.0) >= 10.0
    )
    if not overlay_shell:
        return False

    return True


def _looks_like_listing_modal(snapshot: Dict[str, Any]) -> bool:
    """Heuristic for non-login/listing-content modals such as description overlays."""
    if not _looks_like_blocking_overlay(snapshot):
        return False

    text = snapshot.get("text") or ""
    close_affordance = int(snapshot.get("closeButtonCount") or 0) > 0
    corner_affordance = int(snapshot.get("cornerButtonCount") or 0) > 0
    scrollable = (snapshot.get("scrollHeight") or 0) > (snapshot.get("clientHeight") or 0) + 60

    return close_affordance or corner_affordance or scrollable or len(text) >= 80


async def _vision_confirms_listing_surface_ready(page, log, cancel_event=None) -> bool:
    """Use vision as a tiebreaker when the DOM still claims a modal is present."""
    if cancel_event and cancel_event.is_set():
        raise SessionCancelled("Session terminated by user")

    screenshot = await page.screenshot(type="png")
    action = analyze_screen(
        screenshot,
        goal=(
            "Decide whether a blocking Airbnb listing modal or overlay is still open. "
            "If the normal listing page is visible and usable, respond with done. "
            "If a blocking modal is still open, click its close X or use Escape. "
            "Do not click background listing content."
        ),
        context=(
            "This is a verification check. Respond with done only when the listing page is usable "
            "without any blocking overlay. A leftover invisible DOM container does not count as open."
        ),
        viewport=page.viewport_size,
    )
    log(f"Vision [listing surface check] -> {action.get('action')}: {action.get('reason', '')[:100]}")
    return action.get("action") == "done"


# ---------------------------------------------------------------------------
# Action executor
# ---------------------------------------------------------------------------

async def _execute_action(page, action: Dict[str, Any], log):
    """Translate a vision action dict into raw Playwright IO."""
    act = action.get("action", "wait")
    reason = action.get("reason", "")

    if act == "click":
        x, y = action.get("x", 0), action.get("y", 0)
        log(f"Click ({x}, {y}): {reason}")
        await human_click(page, x, y)

    elif act == "type":
        text = action.get("text", "")
        log(f"Type '{text[:40]}...': {reason}")
        await human_type(page, text)

    elif act == "scroll":
        direction = action.get("direction", "down")
        amount = action.get("amount", 300)
        log(f"Scroll {direction} {amount}px: {reason}")
        await human_scroll(page, direction, amount)

    elif act == "key":
        key = action.get("key", "Enter")
        log(f"Press key '{key}': {reason}")
        await human_press_key(page, key)

    elif act == "wait":
        log(f"Wait: {reason}")
        await random_delay(1.5, 3.0)

    elif act == "done":
        log(f"Phase done: {reason}")

    else:
        log(f"Unknown action '{act}': {reason}")
        await random_delay(1.0, 2.0)


# ---------------------------------------------------------------------------
# Vision loop: see -> think -> act
# ---------------------------------------------------------------------------

async def _vision_loop(
    page,
    goal: str,
    context: str,
    max_steps: int,
    log,
    cancel_event: Optional[threading.Event] = None,
    debug_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run the see-think-act loop for a single phase.
    Returns the final action's data (useful when action is 'done').
    """
    viewport = page.viewport_size
    last_action: Dict[str, Any] = {}

    for step in range(max_steps):
        if cancel_event and cancel_event.is_set():
            raise SessionCancelled("Session terminated by user")

        screenshot = await page.screenshot(type="png")

        if debug_dir:
            ts = datetime.utcnow().strftime("%H%M%S_%f")
            fname = os.path.join(debug_dir, f"step_{ts}.png")
            try:
                with open(fname, "wb") as f:
                    f.write(screenshot)
            except OSError:
                pass

        action = analyze_screen(screenshot, goal=goal, context=context, viewport=viewport)
        log(f"Vision [{goal[:50]}] -> {action.get('action')}: {action.get('reason', '')[:80]}")
        await _execute_action(page, action, log)

        await random_delay(0.8, 2.5)

        last_action = action
        if action.get("action") == "done":
            break

    return last_action


# ---------------------------------------------------------------------------
# Phase runners
# ---------------------------------------------------------------------------

async def _phase_navigate(page, log, cancel_event=None, debug_dir=None):
    """Navigate to airbnb.com and dismiss any initial popups."""
    log("Navigating to Airbnb")
    await page.goto("https://www.airbnb.com/", wait_until="domcontentloaded")
    await random_delay(3.0, 5.0)

    await page.keyboard.press("Escape")
    await random_delay(0.5, 1.0)

    for label in ["Got it", "Close", "Accept", "OK", "Dismiss"]:
        try:
            btn = page.get_by_role("button", name=label)
            if await btn.count() > 0 and await btn.first.is_visible():
                await btn.first.click(timeout=2000)
                log(f"Dismissed popup with '{label}' button")
                await random_delay(0.5, 1.0)
        except Exception:
            pass

    await page.keyboard.press("Escape")
    await random_delay(0.5, 1.0)

    if "airbnb.com" not in page.url:
        log(f"Navigated away to {page.url}, returning to Airbnb")
        await page.goto("https://www.airbnb.com/", wait_until="domcontentloaded")
        await random_delay(2.0, 3.0)


async def _dismiss_blocking_dialog(page, log) -> bool:
    """Close guest-blocking Airbnb dialogs such as translation or login prompts."""
    candidates = page.locator(MODAL_CANDIDATE_SELECTOR)
    snapshots = await _modal_candidate_snapshots(page, limit=6)

    for snapshot in snapshots:
        dialog_label = _match_dialog_signature(snapshot.get("text") or "")
        if not dialog_label or not _looks_like_blocking_overlay(snapshot):
            continue

        idx = snapshot["idx"]
        dialog = candidates.nth(idx)

        log(f"{dialog_label.title()} detected, closing upper-left X")

        dialog_box = snapshot.get("rect") or None

        close_btn = None
        close_box = None
        button_count = await dialog.locator("button").count()
        for btn_idx in range(min(button_count, 8)):
            btn = dialog.locator("button").nth(btn_idx)
            try:
                if not await btn.is_visible():
                    continue
                box = await btn.bounding_box(timeout=1000)
            except Exception:
                continue

            if not box or not dialog_box:
                continue

            center_x = box["x"] + (box["width"] / 2)
            center_y = box["y"] + (box["height"] / 2)
            within_left = center_x <= dialog_box["x"] + (dialog_box["width"] * 0.35)
            within_top = center_y <= dialog_box["y"] + (dialog_box["height"] * 0.22)
            if within_left and within_top:
                close_btn = btn
                close_box = box
                break

        try:
            if close_box:
                click_x = int(close_box["x"] + close_box["width"] * random.uniform(0.35, 0.65))
                click_y = int(close_box["y"] + close_box["height"] * random.uniform(0.35, 0.65))
                await human_click(page, click_x, click_y)
            elif close_btn:
                await close_btn.click(timeout=2000)
            elif dialog_box:
                # Airbnb renders this close affordance as a small X in the modal's
                # upper-left corner, so fall back to that area if the button isn't labeled.
                click_x = int(dialog_box["x"] + min(56, dialog_box["width"] * 0.08))
                click_y = int(dialog_box["y"] + min(52, dialog_box["height"] * 0.08))
                await human_click(page, click_x, click_y)
            else:
                await page.keyboard.press("Escape")
        except Exception as exc:
            log(f"  {dialog_label.title()} close failed: {exc}")
            return False

        await random_delay(0.6, 1.2)
        return True

    return False


async def _click_el(page, locator, desc: str, log):
    """Locate an element via Playwright, then click it with human-like mouse movement."""
    await _dismiss_blocking_dialog(page, log)

    try:
        await locator.scroll_into_view_if_needed(timeout=3000)
        await random_delay(0.2, 0.5)
    except Exception:
        pass

    try:
        box = await locator.bounding_box(timeout=10_000)
    except Exception:
        box = None
    if box:
        viewport = page.viewport_size or {"width": 0, "height": 0}
        if (
            box["x"] + box["width"] < 0
            or box["y"] + box["height"] < 0
            or box["x"] > viewport["width"]
            or box["y"] > viewport["height"]
        ):
            log(f"  -> {desc} box is offscreen, using direct click fallback")
            box = None

    if box:
        x = box["x"] + box["width"] * random.uniform(0.25, 0.75)
        y = box["y"] + box["height"] * random.uniform(0.25, 0.75)
        log(f"  -> {desc} at ({int(x)}, {int(y)})")
        await human_click(page, int(x), int(y))
    else:
        log(f"  -> {desc} (fallback direct click)")
        try:
            await locator.click(timeout=10_000)
        except Exception as exc:
            log(f"  -> click failed for {desc}: {exc}")
            dismissed = await _dismiss_blocking_dialog(page, log)
            if dismissed:
                await random_delay(0.5, 1.0)
                try:
                    await locator.click(timeout=10_000)
                    return
                except Exception as retry_exc:
                    log(f"  -> retry after closing blocking dialog failed: {retry_exc}")
                    raise retry_exc
            log(f"  -> FAILED to click {desc}: {exc}")
            raise


async def _ensure_month_visible(page, month_name: str, year: int, log):
    """Click the forward arrow until the desired month heading is visible."""
    for _ in range(12):
        heading = page.get_by_role("heading", name=f"{month_name} {year}")
        if await heading.count() > 0 and await heading.first.is_visible():
            return
        fwd = page.get_by_role(
            "button", name="Move forward to switch to the next month."
        )
        if await fwd.count() == 0:
            break
        try:
            if await fwd.first.is_disabled():
                break
        except Exception:
            pass
        await _click_el(page, fwd.first, "calendar forward", log)
        await random_delay(0.4, 0.8)


async def _first_visible(locator, limit: int = 8):
    """Return the first visible locator match, or None if nothing is visible."""
    try:
        count = await locator.count()
    except Exception:
        return None

    for idx in range(min(count, limit)):
        candidate = locator.nth(idx)
        try:
            if await candidate.is_visible():
                return candidate
        except Exception:
            continue
    return None


async def _find_location_entry_control(page, log):
    """Find the location entry point for either the expanded or compact search UI."""
    candidates = [
        (
            "Where search box",
            page.get_by_role("searchbox", name=re.compile(r"^(Where|Search destinations)$", re.I)),
        ),
        (
            "compact location button",
            page.get_by_role("button", name=re.compile(r"^(Location\s+)?(Anywhere|Search destinations|Where)$", re.I)),
        ),
        (
            "compact location button",
            page.locator(
                'button:has-text("Anywhere"), '
                'button:has-text("Search destinations"), '
                'button:has-text("Location")'
            ),
        ),
    ]

    for desc, locator in candidates:
        match = await _first_visible(locator)
        if match:
            return match, desc

    log("Location entry not immediately visible, scrolling to page top and retrying")
    await page.keyboard.press("Home")
    await random_delay(1.0, 2.0)
    await _dismiss_blocking_dialog(page, log)

    for desc, locator in candidates:
        match = await _first_visible(locator)
        if match:
            return match, desc

    raise RuntimeError(f"Could not find Airbnb location search control on {page.url}")


async def _focus_location_input_if_present(page, log):
    """Focus the visible location input if Airbnb opens one after clicking the entry control."""
    candidates = [
        (
            "location search box",
            page.get_by_role("searchbox", name=re.compile(r"Where|Search destinations", re.I)),
        ),
        (
            "location search box",
            page.get_by_role("searchbox"),
        ),
        (
            "location text box",
            page.get_by_role("textbox"),
        ),
        (
            "location input",
            page.locator(
                'input[placeholder*="Search"], '
                'input[placeholder*="Where"], '
                'input[aria-label*="Search"], '
                'input[aria-label*="Where"], '
                'input[type="text"]'
            ),
        ),
    ]

    for desc, locator in candidates:
        match = await _first_visible(locator)
        if match:
            await _click_el(page, match, desc, log)
            await random_delay(0.3, 0.8)
            return True
    return False


async def _location_input_focused(page) -> bool:
    """Return True when the active element looks like Airbnb's destination input."""
    try:
        return bool(await page.evaluate(
            """
            () => {
              const el = document.activeElement;
              if (!el) return false;
              const tag = (el.tagName || "").toLowerCase();
              const role = (el.getAttribute("role") || "").toLowerCase();
              const label = (el.getAttribute("aria-label") || "").toLowerCase();
              const placeholder = (el.getAttribute("placeholder") || "").toLowerCase();
              return (
                tag === "input" ||
                tag === "textarea" ||
                role === "searchbox" ||
                role === "textbox" ||
                label.includes("search") ||
                label.includes("where") ||
                placeholder.includes("search") ||
                placeholder.includes("where")
              );
            }
            """
        ))
    except Exception:
        return False


async def _calendar_picker_visible(page) -> bool:
    """Detect whether Airbnb's date picker is already open."""
    day_button = await _first_visible(
        page.get_by_role("button", name=re.compile(r"^\d{1,2}, .+ \d{4}$")),
        limit=12,
    )
    if day_button:
        return True

    next_month = await _first_visible(
        page.get_by_role(
            "button",
            name=re.compile(r"Move forward to switch to the next month", re.I),
        ),
        limit=2,
    )
    return next_month is not None


async def _wait_for_location_search_surface(page, log, attempts: int = 8):
    """Wait for Airbnb's destination search UI to become usable."""
    for attempt in range(attempts):
        await _dismiss_blocking_dialog(page, log)

        if await _location_input_focused(page):
            return True
        if await _calendar_picker_visible(page):
            return True
        if await _first_visible(page.get_by_role("option"), limit=6):
            return True
        if await _first_visible(page.get_by_role("listbox"), limit=2):
            return True
        if await _first_visible(page.get_by_role("searchbox"), limit=4):
            return True
        if await _first_visible(page.get_by_role("textbox"), limit=4):
            return True

        if attempt < attempts - 1:
            await asyncio.sleep(0.6)

    return False


async def _select_first_location_suggestion(page, log):
    """Select the first visible location suggestion across Airbnb search variants."""
    candidates = [
        (
            "first suggestion",
            page.get_by_role("listbox", name=re.compile(r"Search suggestions", re.I)).get_by_role("option"),
        ),
        (
            "first suggestion",
            page.get_by_role("listbox").get_by_role("option"),
        ),
        (
            "first suggestion",
            page.get_by_role("option"),
        ),
    ]

    for desc, locator in candidates:
        match = await _first_visible(locator)
        if match:
            await _click_el(page, match, desc, log)
            return

    raise RuntimeError(f"Could not find a visible Airbnb location suggestion on {page.url}")


async def _vision_search_recovery(page, search_area: str, log, cancel_event=None):
    """Use screenshot-based recovery when Airbnb's destination UI isn't stable."""
    goal = (
        f"You are on Airbnb and need to finish choosing the destination '{search_area}'. "
        "If a destination suggestion matching that place is visible, click it. "
        "If the location input field or destination search area is visible but not active, click it. "
        "If a loading dialog or spinner is still visible, wait. "
        "If the destination is already selected and the calendar/date picker is open, respond with done. "
        "Do not click listing cards, the Airbnb logo, or unrelated navigation."
    )
    context = (
        "Focus only on the destination search UI near the top of the page or inside any open search overlay. "
        "Behave like a guest trying to finish the location step."
    )
    return await _vision_loop(
        page,
        goal=goal,
        context=context,
        max_steps=4,
        log=log,
        cancel_event=cancel_event,
    )


async def _clear_and_retype_location(page, search_area: str, log):
    """Retype the destination query after refocusing the location input."""
    if not await _location_input_focused(page):
        focused = await _focus_location_input_if_present(page, log)
        if not focused:
            return False

    try:
        await page.keyboard.press("Meta+A")
        await asyncio.sleep(0.1)
        await page.keyboard.press("Backspace")
    except Exception:
        pass

    log(f"Retyping location: {search_area}")
    await human_type(page, search_area)
    await random_delay(1.2, 2.0)
    return True


async def _prepare_location_entry(page, search_area: str, log, cancel_event=None):
    """Make sure the destination entry step is actually ready before typing."""
    for attempt in range(3):
        ready = await _wait_for_location_search_surface(page, log, attempts=5)
        if await _calendar_picker_visible(page):
            return True
        if await _location_input_focused(page):
            return True
        if await _focus_location_input_if_present(page, log):
            if await _location_input_focused(page):
                return True

        if attempt < 2:
            log("Location input not ready yet, reopening destination entry")
            search_entry, search_desc = await _find_location_entry_control(page, log)
            await _click_el(page, search_entry, search_desc, log)
            await random_delay(0.7, 1.5)

    log("DOM location-entry recovery failed, trying vision fallback")
    await _vision_search_recovery(page, search_area, log, cancel_event)
    await _wait_for_location_search_surface(page, log, attempts=6)
    return await _location_input_focused(page) or await _calendar_picker_visible(page)


async def _choose_location_suggestion_hybrid(page, search_area: str, log, cancel_event=None):
    """Select the destination suggestion with DOM first, then vision fallback."""
    for attempt in range(3):
        if await _calendar_picker_visible(page):
            return True

        try:
            await _select_first_location_suggestion(page, log)
            await random_delay(1.0, 2.0)
            if await _calendar_picker_visible(page):
                return True
        except RuntimeError as exc:
            log(f"DOM suggestion selection unavailable: {exc}")

        await _wait_for_location_search_surface(page, log, attempts=6)
        if await _calendar_picker_visible(page):
            return True

        if attempt < 2:
            retyped = await _clear_and_retype_location(page, search_area, log)
            if retyped:
                continue

        log("Falling back to vision to finish destination selection")
        await _vision_search_recovery(page, search_area, log, cancel_event)
        await _wait_for_location_search_surface(page, log, attempts=6)

    return await _calendar_picker_visible(page)


async def _phase_search(page, search_area: str, checkin: date, checkout: date, log,
                        cancel_event=None, debug_dir=None):
    """Enter location, dates, and click search using direct DOM interactions
    with human-like click behavior."""

    def _cancel_check():
        if cancel_event and cancel_event.is_set():
            raise SessionCancelled("Session terminated by user")

    # -- 1. Open Airbnb's destination entry UI ------------------------------
    _cancel_check()
    log("Clicking search box")
    search_entry, search_desc = await _find_location_entry_control(page, log)
    await _click_el(page, search_entry, search_desc, log)
    await random_delay(0.5, 1.5)
    destination_ready = await _prepare_location_entry(
        page, search_area, log, cancel_event=cancel_event
    )
    if not destination_ready:
        raise RuntimeError(
            f"Could not prepare Airbnb destination entry for {search_area!r} on {page.url}"
        )

    # -- 2. Enter and confirm the destination -------------------------------
    if await _calendar_picker_visible(page):
        log("Destination already selected; calendar is open")
    else:
        _cancel_check()
        typed = await _clear_and_retype_location(page, search_area, log)
        if not typed:
            log("Location input still not editable, retrying destination preparation")
            destination_ready = await _prepare_location_entry(
                page, search_area, log, cancel_event=cancel_event
            )
            if not destination_ready:
                raise RuntimeError(
                    f"Airbnb destination UI never became editable for {search_area!r} on {page.url}"
                )
            if not await _calendar_picker_visible(page):
                typed = await _clear_and_retype_location(page, search_area, log)

        if not typed and not await _calendar_picker_visible(page):
            raise RuntimeError(
                f"Could not type Airbnb destination {search_area!r} on {page.url}"
            )

        _cancel_check()
        log("Selecting location suggestion")
        suggestion_selected = await _choose_location_suggestion_hybrid(
            page, search_area, log, cancel_event=cancel_event
        )
        if not suggestion_selected:
            raise RuntimeError(
                f"Could not confirm Airbnb destination suggestion for {search_area!r} on {page.url}"
            )

    # After selecting a suggestion the calendar auto-opens.
    # -- 4. Pick check-in date ----------------------------------------------
    _cancel_check()
    ci_month = checkin.strftime("%B")
    ci_year = checkin.year
    log(f"Selecting check-in: {checkin.isoformat()}")
    await _ensure_month_visible(page, ci_month, ci_year, log)
    ci_pattern = re.compile(
        rf"^{checkin.day}, \w+, {ci_month} {ci_year}"
    )
    ci_btn = page.get_by_role("button", name=ci_pattern)
    await _click_el(page, ci_btn, f"check-in day {checkin.day}", log)
    await random_delay(0.8, 1.5)

    # -- 5. Pick check-out date ---------------------------------------------
    _cancel_check()
    co_month = checkout.strftime("%B")
    co_year = checkout.year
    log(f"Selecting check-out: {checkout.isoformat()}")
    await _ensure_month_visible(page, co_month, co_year, log)
    co_pattern = re.compile(
        rf"^{checkout.day}, \w+, {co_month} {co_year}"
    )
    co_btn = page.get_by_role("button", name=co_pattern)
    await _click_el(page, co_btn, f"check-out day {checkout.day}", log)
    await random_delay(0.8, 1.5)

    # -- 6. Click the search button -----------------------------------------
    _cancel_check()
    log("Clicking search button")
    search_btn = page.get_by_role("button", name="Search")
    await _click_el(page, search_btn, "Search button", log)
    await random_delay(4.0, 7.0)

    current_url = page.url
    log(f"Post-search URL: {current_url}")
    if "/s/" not in current_url:
        log("Search didn't navigate, retrying")
        await _click_el(page, search_btn, "Search button retry", log)
        await random_delay(4.0, 7.0)

    await page.keyboard.press("Escape")
    await random_delay(0.5, 1.0)

    if "search_by_map" not in page.url:
        sep = "&" if "?" in page.url else "?"
        await page.goto(page.url + sep + "search_by_map=true", wait_until="domcontentloaded")
        await random_delay(2.0, 3.0)
        log("Enabled map view (search_by_map=true)")


async def _get_listing_count(page, log) -> Optional[int]:
    """Read the listing count from the search results heading.

    Airbnb headings look like:
      'Over 1,000 homes in Pigeon Forge'
      '343 homes within map area'
      'Over 1,000 national park homes'
    Returns the parsed count, or 9999 if the heading says 'Over 1,000'.
    """
    try:
        text = await page.locator("h1").first.inner_text(timeout=5000)
        text = text.strip()
        log(f"Heading text: {text}")

        over = "over" in text.lower()
        m = re.search(r"([\d,]+)", text)
        if m:
            count = int(m.group(1).replace(",", ""))
            if over:
                count = max(count, 1000)
            log(f"Listing count: {count}{' (over)' if over else ''}")
            return count
    except Exception as e:
        log(f"Could not read listing count: {e}")
    return None


async def _phase_zoom_to_target(
    page, target_lat: Optional[float], target_lng: Optional[float], log,
    cancel_event=None, debug_dir=None,
):
    """If there are too many listings, narrow the search area by adding
    bounding box URL parameters centered on the target listing's coordinates.

    Airbnb URL params: ne_lat, ne_lng, sw_lat, sw_lng define the map bounding box.
    We adjust this box until the listing count settles into a usable browsing
    range for the left panel.
    """
    if not target_lat or not target_lng:
        log("No target lat/lng available, skipping zoom")
        return

    from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

    def _extract_half_span(url: str) -> Optional[float]:
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query, keep_blank_values=True)
            ne_lat = float(params["ne_lat"][0])
            sw_lat = float(params["sw_lat"][0])
            ne_lng = float(params["ne_lng"][0])
            sw_lng = float(params["sw_lng"][0])
            lat_half = abs(ne_lat - sw_lat) / 2.0
            lng_half = abs(ne_lng - sw_lng) / 2.0
            span = max(lat_half, lng_half)
            return span if span > 0 else None
        except Exception:
            return None

    await random_delay(2.0, 3.0)
    count = await _get_listing_count(page, log)
    if count is not None and MAP_LISTING_COUNT_MIN <= count <= MAP_LISTING_COUNT_MAX:
        log(
            f"Listing count OK ({count}) within target range "
            f"{MAP_LISTING_COUNT_MIN}-{MAP_LISTING_COUNT_MAX}, no zoom needed"
        )
        return

    half_span = _extract_half_span(page.url) or 0.08
    if count is not None and count < MAP_LISTING_COUNT_MIN and "ne_lat" not in page.url:
        half_span = max(half_span, 0.12)

    for attempt in range(8):
        if cancel_event and cancel_event.is_set():
            raise SessionCancelled("Session terminated by user")

        if count is not None:
            if MAP_LISTING_COUNT_MIN <= count <= MAP_LISTING_COUNT_MAX:
                log(
                    f"Zoom complete: {count} listings within target range "
                    f"{MAP_LISTING_COUNT_MIN}-{MAP_LISTING_COUNT_MAX} at ±{half_span:.3f}°"
                )
                return
            if count > MAP_LISTING_COUNT_MAX:
                half_span = max(half_span * 0.62, 0.002)
                zoom_direction = "in"
            else:
                half_span = min(half_span * 1.45, 0.5)
                zoom_direction = "out"
        else:
            zoom_direction = "in"

        ne_lat = target_lat + half_span
        ne_lng = target_lng + half_span
        sw_lat = target_lat - half_span
        sw_lng = target_lng - half_span

        count_label = f"{count}" if count is not None else "unknown"
        log(
            f"Zoom attempt {attempt + 1}: zooming {zoom_direction} to ±{half_span:.3f}° "
            f"around target (count={count_label}, target={MAP_LISTING_COUNT_MIN}-{MAP_LISTING_COUNT_MAX})"
        )

        parsed = urlparse(page.url)
        params = parse_qs(parsed.query, keep_blank_values=True)
        params["ne_lat"] = [str(round(ne_lat, 6))]
        params["ne_lng"] = [str(round(ne_lng, 6))]
        params["sw_lat"] = [str(round(sw_lat, 6))]
        params["sw_lng"] = [str(round(sw_lng, 6))]
        params["search_by_map"] = ["true"]

        flat_params = {k: v[0] if isinstance(v, list) and len(v) == 1 else v for k, v in params.items()}
        new_url = urlunparse(parsed._replace(query=urlencode(flat_params, doseq=True)))

        await page.goto(new_url, wait_until="domcontentloaded")
        await random_delay(3.0, 5.0)

        count = await _get_listing_count(page, log)
        if count is not None and MAP_LISTING_COUNT_MIN <= count <= MAP_LISTING_COUNT_MAX:
            log(
                f"Zoom complete: {count} listings within target range "
                f"{MAP_LISTING_COUNT_MIN}-{MAP_LISTING_COUNT_MAX} at ±{half_span:.3f}°"
            )
            return

    log(
        f"Zoom limit reached with count {count}; continuing to browse results even though "
        f"it is outside the target range {MAP_LISTING_COUNT_MIN}-{MAP_LISTING_COUNT_MAX}"
    )


async def _find_target_position(page, target_id: str) -> Optional[int]:
    """Check all listing links on the current page and return the 1-based
    position of the target listing, or None if not found."""
    all_links = page.locator('a[href*="/rooms/"]')
    count = await all_links.count()
    seen_ids: list = []
    for idx in range(count):
        try:
            href = await all_links.nth(idx).get_attribute("href", timeout=2000)
            if not href:
                continue
            m = re.search(r"/rooms/(\d+)", href)
            if m:
                lid = m.group(1)
                if lid not in seen_ids:
                    seen_ids.append(lid)
                if lid == target_id:
                    return len(seen_ids)
        except Exception:
            pass
    return None


async def _move_mouse_to_listings_panel(page, log):
    """Move the mouse to the left listings panel so scroll events don't
    hit the map (which would zoom it out and change results)."""
    vw = page.viewport_size["width"]
    vh = page.viewport_size["height"]
    target_x = int(vw * random.uniform(0.15, 0.35))
    target_y = int(vh * random.uniform(0.35, 0.65))
    log(f"Moving mouse to listings panel ({target_x}, {target_y})")
    await smooth_mouse_move(page, target_x, target_y)
    await random_delay(0.3, 0.6)


async def _scroll_listings_to_bottom(page, target_id: str, log, cancel_event=None):
    """Scroll the left listings panel from top to bottom, checking for the
    target listing along the way.  Returns (found, position) or (False, None).

    Scrolls in human-like increments and keeps the mouse on the left panel
    so the map stays fixed."""
    max_scroll_attempts = 25
    last_scroll_y = -1

    for step in range(max_scroll_attempts):
        if cancel_event and cancel_event.is_set():
            raise SessionCancelled("Session terminated by user")

        position = await _find_target_position(page, target_id)
        if position is not None:
            return True, position

        current_y = await page.evaluate("window.scrollY")
        if step > 3 and current_y == last_scroll_y:
            log(f"  Scroll reached bottom at step {step}")
            break
        last_scroll_y = current_y

        await human_scroll(page, "down", random.randint(400, 700))
        await random_delay(0.8, 2.0)

        if random.random() < 0.12:
            vw = page.viewport_size["width"]
            vh = page.viewport_size["height"]
            wx = int(vw * random.uniform(0.05, 0.40))
            wy = int(vh * random.uniform(0.20, 0.80))
            await smooth_mouse_move(page, wx, wy)
            await random_delay(0.3, 0.8)

    position = await _find_target_position(page, target_id)
    if position is not None:
        return True, position
    return False, None


async def _phase_browse_results(
    page, target_name: str, target_id: str, log,
    cancel_event=None, debug_dir=None,
) -> Tuple[bool, Optional[int], Optional[int]]:
    """
    Scroll through search result pages looking for the target listing.
    On each page: scroll all the way to the bottom of the listings panel,
    then click Next to go to the next page.
    Returns (found, page_number, position_on_page).
    """

    for page_num in range(1, MAX_PAGES_TO_BROWSE + 1):
        if cancel_event and cancel_event.is_set():
            raise SessionCancelled("Session terminated by user")

        log(f"Scanning results page {page_num}")
        await _dismiss_blocking_dialog(page, log)

        await _move_mouse_to_listings_panel(page, log)

        await page.keyboard.press("Home")
        await random_delay(1.0, 2.0)

        await _move_mouse_to_listings_panel(page, log)

        found, position = await _scroll_listings_to_bottom(
            page, target_id, log, cancel_event
        )
        if found:
            log(f"Target found on page {page_num}, position {position}")
            return True, page_num, position

        next_el = page.get_by_role("link", name="Next")
        if await next_el.count() == 0:
            next_el = page.get_by_role("button", name="Next")
        if await next_el.count() == 0:
            next_el = page.locator('a[aria-label="Next"]')

        if await next_el.count() == 0:
            log("No 'Next' button found, last page reached")
            return False, None, None

        try:
            await next_el.first.scroll_into_view_if_needed(timeout=3000)
            await random_delay(0.5, 1.0)
        except Exception:
            pass

        log(f"Target not on page {page_num}, clicking Next")
        await _click_el(page, next_el.first, "Next page", log)
        await random_delay(3.0, 5.0)

        await page.keyboard.press("Escape")
        await random_delay(0.5, 1.0)

    log(f"Target not found after {MAX_PAGES_TO_BROWSE} pages")
    return False, None, None


async def _open_listing_tab(page, link_locator, desc: str, log) -> "Page | None":
    """Click a listing link and capture the new tab it opens.

    Airbnb listing cards use target='_blank', so clicking them opens a
    new browser tab. This helper captures that new tab and returns it.
    Falls back to removing target='_blank' and navigating in-page if the
    popup is not detected."""
    try:
        async with page.context.expect_page(timeout=15000) as new_page_info:
            await _click_el(page, link_locator, desc, log)
        new_page = await new_page_info.value
        await new_page.wait_for_load_state("domcontentloaded")
        await _clear_page_storage(new_page, log)
        await random_delay(2.0, 4.0)
        return new_page
    except Exception:
        log(f"  New tab not detected for {desc}, trying same-page click")
        try:
            await link_locator.evaluate('el => el.removeAttribute("target")')
            await _click_el(page, link_locator, desc, log)
            await _clear_page_storage(page, log)
            await random_delay(3.0, 5.0)
            return None
        except Exception as e2:
            log(f"  Fallback click also failed: {e2}")
            return None


async def _phase_click_target(page, target_name: str, target_id: str, log,
                              cancel_event=None, debug_dir=None):
    """Click on the target listing from the search results.

    Returns the new Page (tab) that was opened, or None on failure.
    The caller is responsible for closing the returned page."""
    link = page.locator(f'a[href*="/rooms/{target_id}"]').first
    try:
        await link.scroll_into_view_if_needed(timeout=5000)
        await random_delay(0.5, 1.5)

        listing_page = await _open_listing_tab(page, link, f"target listing {target_id}", log)

        if listing_page:
            current_url = listing_page.url
        else:
            current_url = page.url
            listing_page = page

        found_id = _extract_listing_id(current_url)
        if found_id == target_id:
            log(f"Confirmed on target listing page (ID: {found_id})")
        else:
            log(f"Landed on listing {found_id}, expected {target_id}")

        return listing_page
    except Exception as e:
        log(f"Could not click target listing: {e}")
        return None


async def _move_mouse_to_page_center(page):
    """Position the mouse over the main content area of a listing page."""
    vw = page.viewport_size["width"]
    vh = page.viewport_size["height"]
    x = int(vw * random.uniform(0.25, 0.55))
    y = int(vh * random.uniform(0.35, 0.65))
    await smooth_mouse_move(page, x, y)
    await random_delay(0.2, 0.5)


async def _browse_photo_tour(page, is_target: bool, log, cancel_event=None):
    """Browse the Airbnb photo tour page (opened by 'Show all photos').
    This is a full page with a photo grid at top and large photos below.
    Scroll through carefully like a guest, then go back."""

    def _check():
        if cancel_event and cancel_event.is_set():
            raise SessionCancelled("Session terminated by user")

    log("  Photo tour: viewing photo grid")
    await random_delay(2.0, 4.0)
    await _move_mouse_to_page_center(page)

    num_scrolls = random.randint(8, 15) if is_target else random.randint(4, 7)
    for i in range(num_scrolls):
        _check()
        await human_scroll(page, "down", random.randint(350, 650))
        pause = random.uniform(1.5, 4.0) if is_target else random.uniform(1.0, 2.5)
        await asyncio.sleep(pause)

        if random.random() < 0.2:
            await mouse_wander(page)
            await random_delay(0.5, 1.5)

        if random.random() < 0.15 and i > 2:
            await human_scroll(page, "up", random.randint(100, 300))
            await random_delay(0.8, 1.5)

    log(f"  Photo tour: scrolled through {num_scrolls} sections, going back")
    await page.go_back(wait_until="domcontentloaded")
    await random_delay(2.0, 3.0)


async def _find_visible_nonblocking_modal(page):
    """Return the first visible modal that is not a known blocking guest dialog."""
    candidates = page.locator(MODAL_CANDIDATE_SELECTOR)
    snapshots = await _modal_candidate_snapshots(page, limit=6)

    for snapshot in snapshots:
        text = snapshot.get("text") or ""
        if _match_dialog_signature(text):
            continue
        if not _looks_like_listing_modal(snapshot):
            continue
        return candidates.nth(snapshot["idx"])

    return None


async def _close_visible_listing_modal(page, log, cancel_event=None) -> bool:
    """Close the currently visible non-blocking listing modal and verify it is gone."""

    def _check():
        if cancel_event and cancel_event.is_set():
            raise SessionCancelled("Session terminated by user")

    async def _modal_closed() -> bool:
        await asyncio.sleep(0.4)
        return await _find_visible_nonblocking_modal(page) is None

    modal = await _find_visible_nonblocking_modal(page)
    if modal is None:
        return True

    for attempt in range(4):
        _check()
        modal = await _find_visible_nonblocking_modal(page)
        if modal is None:
            return True

        modal_box = None
        try:
            modal_box = await modal.bounding_box(timeout=1000)
        except Exception:
            pass

        close_candidates = [
            modal.get_by_role("button", name=re.compile(r"^close$", re.I)),
            modal.get_by_role("button", name=re.compile(r"close", re.I)),
            modal.locator('button[aria-label*="close" i], button[title*="close" i]'),
        ]

        for close_locator in close_candidates:
            close_btn = await _first_visible(close_locator, limit=10)
            if not close_btn:
                continue

            try:
                await _click_el(page, close_btn, "Close modal", log)
            except Exception:
                continue

            if await _modal_closed():
                return True

        if modal_box:
            corner_button_count = await modal.locator("button").count()
            for button_idx in range(min(corner_button_count, 12)):
                corner_btn = modal.locator("button").nth(button_idx)
                try:
                    if not await corner_btn.is_visible():
                        continue
                    btn_box = await corner_btn.bounding_box(timeout=1000)
                except Exception:
                    continue

                if not btn_box:
                    continue

                center_x = btn_box["x"] + (btn_box["width"] / 2)
                center_y = btn_box["y"] + (btn_box["height"] / 2)
                within_top = center_y <= modal_box["y"] + (modal_box["height"] * 0.18)
                within_left = center_x <= modal_box["x"] + (modal_box["width"] * 0.28)
                within_right = center_x >= modal_box["x"] + (modal_box["width"] * 0.72)
                if not within_top or not (within_left or within_right):
                    continue

                log("  Description modal still open, trying corner close button")
                try:
                    await _click_el(page, corner_btn, "Close modal corner button", log)
                except Exception:
                    continue

                if await _modal_closed():
                    return True

        if modal_box:
            log("  Description modal still open, trying upper-left close area")
            click_x = int(modal_box["x"] + min(52, modal_box["width"] * 0.08))
            click_y = int(modal_box["y"] + min(48, modal_box["height"] * 0.08))
            await human_click(page, click_x, click_y)
            if await _modal_closed():
                return True

        log("  Description modal still open, trying Escape")
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        if await _modal_closed():
            return True

        log("  Description modal still open, trying vision close fallback")
        vision_result = await _vision_loop(
            page,
            goal=(
                "A listing description modal is open on Airbnb. "
                "Click the close X for the open modal. "
                "If the modal is already closed, respond with done. "
                "Do not click listing cards or background content."
            ),
            context=(
                "Focus on the currently open Airbnb modal and its top-left or top-right close affordance. "
                "Behave like a guest exiting the modal to continue browsing the listing."
            ),
            max_steps=2,
            log=log,
            cancel_event=cancel_event,
        )
        if vision_result.get("action") == "done":
            log("  Vision confirms the listing page is usable")
            return True
        if await _modal_closed():
            return True

        await random_delay(0.6, 1.2)

    if await _find_visible_nonblocking_modal(page) is None:
        return True

    if await _vision_confirms_listing_surface_ready(page, log, cancel_event):
        log("  Vision overrode a stale DOM modal signal")
        return True

    return False


async def _ensure_listing_surface_ready(page, log, cancel_event=None) -> bool:
    """Make sure no modal is left open before continuing normal page browsing."""
    await _dismiss_blocking_dialog(page, log)
    modal = await _find_visible_nonblocking_modal(page)
    if modal is None:
        return True

    log("  Listing modal detected, closing it before continuing")
    closed = await _close_visible_listing_modal(page, log, cancel_event)
    if closed:
        return True

    if await _vision_confirms_listing_surface_ready(page, log, cancel_event):
        log("  Vision confirms the listing page is ready despite the DOM modal candidate")
        return True

    return False


async def _browse_description_modal(page, log, cancel_event=None):
    """Browse the 'About this space' modal opened by clicking 'Show more'.
    Scroll within the modal dialog to read the full description, then close it."""

    def _check():
        if cancel_event and cancel_event.is_set():
            raise SessionCancelled("Session terminated by user")

    log("  Description modal: reading full description")
    await random_delay(2.0, 3.0)
    await _dismiss_blocking_dialog(page, log)

    modal = await _find_visible_nonblocking_modal(page)
    if modal is None:
        log("  Description modal: could not find modal, skipping")
        return

    vw = page.viewport_size["width"]
    vh = page.viewport_size["height"]
    modal_x = int(vw * random.uniform(0.35, 0.55))
    modal_y = int(vh * random.uniform(0.40, 0.60))
    await smooth_mouse_move(page, modal_x, modal_y)
    await random_delay(0.5, 1.0)

    num_scrolls = random.randint(5, 10)
    for i in range(num_scrolls):
        _check()
        await human_scroll(page, "down", random.randint(250, 500))
        await asyncio.sleep(random.uniform(1.5, 3.5))

        if random.random() < 0.15:
            await asyncio.sleep(random.uniform(1.0, 2.5))

    log(f"  Description modal: read through {num_scrolls} scroll sections")

    closed = await _close_visible_listing_modal(page, log, cancel_event)
    if not closed:
        raise RuntimeError("Description modal remained open after close retries")
    await random_delay(1.0, 2.0)


async def _open_description_modal(page, is_target: bool, log, cancel_event=None) -> bool:
    """Open the listing description modal if available, searching like a real guest."""

    def _check():
        if cancel_event and cancel_event.is_set():
            raise SessionCancelled("Session terminated by user")

    attempts = 7 if is_target else 3
    entry_patterns = [
        re.compile(r"show more about (this place|this space)", re.I),
        re.compile(r"show more", re.I),
        re.compile(r"about this (place|space)", re.I),
    ]

    for attempt in range(attempts):
        _check()
        await _dismiss_blocking_dialog(page, log)

        candidate_locators = [
            page.get_by_role("button", name=pattern)
            for pattern in entry_patterns
        ] + [
            page.get_by_role("link", name=pattern)
            for pattern in entry_patterns
        ] + [
            page.locator(
                'button:has-text("Show more"), '
                'button:has-text("About this place"), '
                'button:has-text("About this space"), '
                'a:has-text("Show more"), '
                'a:has-text("About this place"), '
                'a:has-text("About this space")'
            )
        ]

        for locator in candidate_locators:
            match = await _first_visible(locator)
            if not match:
                continue

            try:
                label = await match.evaluate(
                    "(el) => (el.getAttribute('aria-label') || el.innerText || '').trim().replace(/\\s+/g, ' ')"
                )
            except Exception:
                label = "Show more description"

            await match.scroll_into_view_if_needed(timeout=3000)
            await random_delay(0.4, 0.9)
            await _click_el(page, match, label or "Show more description", log)
            await random_delay(1.5, 3.0)
            await _browse_description_modal(page, log, cancel_event)
            return True

        if attempt < attempts - 1:
            log("  Description entry not visible yet, scrolling further")
            await human_scroll(page, "down", random.randint(220, 420))
            await random_delay(1.2, 2.3)

    log("  Description entry not found, continuing without modal")
    return False


async def _phase_engage(page, listing_name: str, is_target: bool, log,
                        cancel_event=None, debug_dir=None):
    """Browse a listing page like a real guest would.

    Simulates realistic human behavior:
    1. Read the top section (title, host info, highlights)
    2. Click "Show more" to read full description in modal, scroll through it
    3. Scroll down through amenities, sleeping arrangements
    4. Click "Show all photos" to view photo tour, scroll through all photos
    5. Continue scrolling through reviews, location, policies
    6. Scroll all the way to the bottom
    7. Go back to search results
    """
    label = "target" if is_target else "other"

    def _check_cancel():
        if cancel_event and cancel_event.is_set():
            raise SessionCancelled("Session terminated by user")

    await _dismiss_blocking_dialog(page, log)

    # --- 1. Read the top section (title, host, highlights) ----------------
    log(f"Engaging {label} listing: reading top section")
    await _move_mouse_to_page_center(page)
    await random_delay(3.0, 5.0)
    await mouse_wander(page)
    await reading_pause(random.randint(300, 500))
    _check_cancel()

    # --- 2. Scroll down to description and click "Show more" --------------
    log(f"Engaging {label} listing: scrolling to description")
    await _move_mouse_to_page_center(page)
    for _ in range(random.randint(2, 4)):
        await human_scroll(page, "down", random.randint(300, 500))
        await random_delay(1.5, 3.0)
    _check_cancel()

    if is_target or random.random() < 0.4:
        log(f"Engaging {label} listing: opening full description")
        try:
            await _open_description_modal(page, is_target, log, cancel_event)
        except Exception as e:
            log(f"  Show more skipped: {e}")
        if not await _ensure_listing_surface_ready(page, log, cancel_event):
            raise RuntimeError("Could not return to listing page after reading the description modal")
    _check_cancel()

    # --- 3. Continue scrolling through amenities, sleeping arrangements ---
    log(f"Engaging {label} listing: reading amenities & rooms")
    if not await _ensure_listing_surface_ready(page, log, cancel_event):
        raise RuntimeError("Listing modal remained open before amenities browsing")
    await _move_mouse_to_page_center(page)
    scroll_count = random.randint(3, 5) if is_target else random.randint(1, 3)
    for _ in range(scroll_count):
        _check_cancel()
        await human_scroll(page, "down", random.randint(400, 700))
        await random_delay(1.5, 3.5)
        if random.random() < 0.3:
            await reading_pause(random.randint(200, 400))
    _check_cancel()

    # --- 4. Click "Show all photos" to view the photo tour ----------------
    log(f"Engaging {label} listing: opening photo tour")
    try:
        if not await _ensure_listing_surface_ready(page, log, cancel_event):
            raise RuntimeError("Listing modal remained open before photo browsing")
        await page.keyboard.press("Home")
        await random_delay(1.0, 2.0)
        await _move_mouse_to_page_center(page)

        show_photos = page.get_by_role("button", name="Show all photos")
        if await show_photos.count() > 0:
            await _click_el(page, show_photos.first, "Show all photos", log)
            await random_delay(2.0, 4.0)
            await _browse_photo_tour(page, is_target, log, cancel_event)
        else:
            photo_btns = page.locator('button:has-text("Show all photos")')
            if await photo_btns.count() > 0:
                await _click_el(page, photo_btns.first, "Show all photos", log)
                await random_delay(2.0, 4.0)
                await _browse_photo_tour(page, is_target, log, cancel_event)
            else:
                log("  Photo button not found, skipping")
    except Exception as e:
        log(f"  Photo tour skipped: {e}")
    _check_cancel()

    # --- 5. Scroll through calendar, reviews, host, location, policies ----
    remaining_sections = [
        "calendar & pricing",
        "reviews",
        "host info",
        "location & nearby",
        "house rules & cancellation policy",
    ]
    if not is_target:
        remaining_sections = random.sample(remaining_sections, k=random.randint(2, 3))

    await _move_mouse_to_page_center(page)
    for section in remaining_sections:
        _check_cancel()
        if not await _ensure_listing_surface_ready(page, log, cancel_event):
            raise RuntimeError(f"Listing modal remained open before reading {section}")
        log(f"Engaging {label} listing: reading {section}")
        await human_scroll(page, "down", random.randint(400, 800))
        await random_delay(2.0, 4.0)
        await reading_pause(random.randint(200, 500))

        if random.random() < 0.25:
            await mouse_wander(page)
            await random_delay(0.5, 1.5)

        if random.random() < 0.2:
            await human_scroll(page, "down", random.randint(200, 400))
            await random_delay(1.0, 2.0)
    _check_cancel()

    # --- 6. Scroll all the way to the bottom ------------------------------
    log(f"Engaging {label} listing: scrolling to bottom")
    bottom_scrolls = random.randint(4, 8) if is_target else random.randint(2, 4)
    for _ in range(bottom_scrolls):
        _check_cancel()
        await human_scroll(page, "down", random.randint(500, 900))
        await random_delay(1.0, 2.5)
    await reading_pause(random.randint(200, 400))

    log(f"Finished browsing {label} listing")


async def _phase_browse_others(page, target_id: str, count: int, log,
                               cancel_event=None, debug_dir=None) -> List[str]:
    """Click on 1-2 random other listings and browse them."""
    vl = dict(cancel_event=cancel_event, debug_dir=debug_dir)
    browsed = []

    for i in range(count):
        if cancel_event and cancel_event.is_set():
            raise SessionCancelled("Session terminated by user")

        log(f"Clicking random other listing ({i + 1}/{count})")
        await _move_mouse_to_listings_panel(page, log)
        await human_scroll(page, "down", random.randint(200, 500))
        await random_delay(1.0, 2.0)

        all_links = page.locator('a[href*="/rooms/"]')
        link_count = await all_links.count()
        if link_count == 0:
            log("No listing links found on page")
            continue

        candidates = []
        for j in range(link_count):
            try:
                href = await all_links.nth(j).get_attribute("href", timeout=2000)
                if href:
                    lid = _extract_listing_id(href)
                    if lid and lid != target_id:
                        candidates.append((j, lid))
            except Exception:
                continue

        if not candidates:
            log("No other listing links found")
            continue

        idx, lid = random.choice(candidates)
        link = all_links.nth(idx)

        listing_page = await _open_listing_tab(page, link, f"other listing {lid}", log)
        if listing_page is None:
            listing_page = page

        listing_url = listing_page.url
        found_lid = _extract_listing_id(listing_url)

        if found_lid and found_lid != target_id:
            await _phase_engage(listing_page, f"listing {found_lid}", is_target=False, log=log, **vl)
            browsed.append(found_lid)
        else:
            log(f"Landed on unexpected page ({listing_url}), skipping")

        if listing_page != page:
            await listing_page.close()
            log(f"Closed other listing tab")
        else:
            await page.go_back(wait_until="domcontentloaded")
            await random_delay(1.0, 2.0)

    return browsed


# ---------------------------------------------------------------------------
# Main entry point -- same signature as before
# ---------------------------------------------------------------------------

async def run_boost_session(
    campaign_dict: Dict[str, Any],
    proxy_dict: Optional[Dict[str, Any]] = None,
    headless: bool = True,
    cancel_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    """
    Execute a single vision-driven boost session.

    Returns a result dict with keys:
        target_found, target_page_number, target_position_on_page,
        total_pages_browsed, other_listings_browsed, search_dates,
        session_log, error_message
    """
    from playwright.async_api import async_playwright

    result: Dict[str, Any] = {
        "target_found": False,
        "target_page_number": None,
        "target_position_on_page": None,
        "total_pages_browsed": 0,
        "other_listings_browsed": [],
        "search_dates": None,
        "session_log": [],
        "error_message": None,
    }

    # Create debug directory for screenshots
    session_ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    debug_dir = os.path.join(DEBUG_SCREENSHOTS_DIR, f"session_{session_ts}")
    os.makedirs(debug_dir, exist_ok=True)

    def log(msg: str):
        ts = datetime.utcnow().isoformat()
        result["session_log"].append({"t": ts, "msg": msg})
        logger.info(f"[boost] {msg}")

    target_id = _extract_listing_id(campaign_dict["target_listing_url"])
    if not target_id:
        result["error_message"] = "Could not extract listing ID from target URL"
        return result

    target_name = campaign_dict.get("target_listing_name") or f"Listing {target_id}"

    window_start = date.fromisoformat(campaign_dict["date_window_start"])
    window_end = date.fromisoformat(campaign_dict["date_window_end"])
    min_nights = campaign_dict.get("min_nights", 2)
    max_nights = campaign_dict.get("max_nights", 5)

    available_windows = None
    listing_id = campaign_dict.get("target_listing_id")
    if listing_id:
        from dashboard.boost.listing_helper import get_available_windows
        log(f"Fetching calendar availability for listing {listing_id}...")
        available_windows = get_available_windows(
            listing_id, window_start, window_end, min_nights,
        )
        if available_windows:
            log(f"Found {len(available_windows)} available window(s) "
                f"with >= {min_nights} nights")
        else:
            log("WARNING: No available windows found in calendar. "
                "Dates will be picked from campaign window but listing "
                "may not appear in Airbnb search results.")

    checkin, checkout = _pick_search_dates(
        window_start, window_end, min_nights, max_nights,
        available_windows=available_windows,
    )
    result["search_dates"] = {
        "checkin": checkin.isoformat(),
        "checkout": checkout.isoformat(),
    }
    log(f"Search dates: {checkin} to {checkout}")

    viewport = get_random_viewport()
    user_agent = get_random_user_agent()
    locale = get_random_locale()
    timezone = get_random_timezone()

    vl = dict(cancel_event=cancel_event, debug_dir=debug_dir)

    async with async_playwright() as pw:
        launch_kwargs: Dict[str, Any] = {
            "headless": headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
            ],
        }
        if proxy_dict:
            launch_kwargs["proxy"] = proxy_dict
            log(f"Using proxy: {proxy_dict.get('server', 'unknown')}")

        browser = await pw.chromium.launch(**launch_kwargs)

        video_dir = os.path.join(debug_dir, "video")
        os.makedirs(video_dir, exist_ok=True)

        log("Using a fresh incognito browser context with empty cookies and storage")
        context = await browser.new_context(
            viewport=viewport,
            user_agent=user_agent,
            locale=locale,
            timezone_id=timezone,
            color_scheme=random.choice(["light", "no-preference"]),
            java_script_enabled=True,
            storage_state={"cookies": [], "origins": []},
            service_workers="block",
            record_video_dir=video_dir,
            record_video_size={"width": viewport["width"], "height": viewport["height"]},
        )

        await context.clear_cookies()

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            window.chrome = { runtime: {} };
        """)
        await context.add_init_script("""
            (() => {
                try { window.localStorage.clear(); } catch (e) {}
                try { window.sessionStorage.clear(); } catch (e) {}
                try {
                    if ('caches' in window) {
                        caches.keys().then((keys) => Promise.all(keys.map((key) => caches.delete(key)))).catch(() => {});
                    }
                } catch (e) {}
            })();
        """)

        page = await context.new_page()
        page.set_default_timeout(30_000)
        await _clear_page_storage(page, log)

        try:
            await _phase_navigate(page, log, **vl)
            await _phase_search(page, campaign_dict["search_area"], checkin, checkout, log, **vl)

            target_lat = campaign_dict.get("target_lat")
            target_lng = campaign_dict.get("target_lng")
            await _phase_zoom_to_target(page, target_lat, target_lng, log, **vl)

            target_found, page_num, position = await _phase_browse_results(
                page, target_name, target_id, log, **vl,
            )
            result["target_found"] = target_found
            result["target_page_number"] = page_num
            result["target_position_on_page"] = position
            result["total_pages_browsed"] = page_num if page_num else 1

            if target_found:
                listing_page = await _phase_click_target(page, target_name, target_id, log, **vl)
                if listing_page:
                    await _phase_engage(listing_page, target_name, is_target=True, log=log, **vl)
                    if listing_page != page:
                        await listing_page.close()
                        log("Closed target listing tab, back on search results")
                    else:
                        await page.go_back(wait_until="domcontentloaded")
                        await random_delay(2.0, 3.0)
                    log("Finished engaging with target listing")
            else:
                log("Target listing not found in search results")

            others_count = random.randint(1, 2)
            other_listings = await _phase_browse_others(
                page, target_id, others_count, log, **vl,
            )
            result["other_listings_browsed"] = other_listings

        except SessionCancelled:
            log("Session cancelled by user")
            result["error_message"] = "Session cancelled by user"
        except Exception as e:
            result["error_message"] = str(e)
            log(f"Error: {e}")
            logger.exception("Boost session error")
        finally:
            await random_delay(1.0, 3.0)
            await context.close()
            await browser.close()

    log(f"Debug screenshots saved to: {debug_dir}")
    return result
