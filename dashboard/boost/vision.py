#!/usr/bin/env python3
"""
GPT-4o vision integration for screen-driven browser automation.

Takes screenshots, sends them to the vision model with goal-specific prompts,
and returns structured actions (click, type, scroll, wait, done).
"""

import base64
import io
import json
import logging
from typing import Any, Dict, Optional

from openai import OpenAI
from PIL import Image

import dashboard.config as config

logger = logging.getLogger(__name__)

VISION_MODEL = "gpt-5.4"

SCREENSHOT_MAX_WIDTH = 1280
SCREENSHOT_JPEG_QUALITY = 80

SYSTEM_PROMPT = """\
You are a human guest casually browsing Airbnb to find a vacation rental.
You are looking at a browser screenshot. Your job is to decide what a real
person would do next to accomplish the current goal.

IMPORTANT RULES:
- Always respond with a single JSON object, nothing else.
- Coordinates (x, y) are pixel positions ON THE SCREENSHOT IMAGE you see.
  Do NOT try to scale or adjust them. Return the exact pixel position on the
  image where you want to click.
- Be precise with coordinates -- click the CENTER of the target element.
- When you see a text input that is already focused, use "type" instead of "click".
- When scrolling, specify direction ("up" or "down") and amount in pixels (200-500).
- Use "wait" if the page appears to be loading.
- Use "done" when the current goal has been achieved, include any relevant data.
- Never mention that you are an AI or an automated agent.
- Do NOT click on the Airbnb logo or navigation links unless specifically asked.
- If there is no popup or overlay visible, respond with "done" instead of
  clicking random elements.

RESPONSE FORMAT (strict JSON, no markdown fences):
{"action": "click", "x": <int>, "y": <int>, "reason": "<brief explanation>"}
{"action": "type", "text": "<text to type>", "reason": "<brief explanation>"}
{"action": "scroll", "direction": "down"|"up", "amount": <int 200-500>, "reason": "..."}
{"action": "key", "key": "<key name e.g. Enter, Escape, Tab>", "reason": "..."}
{"action": "wait", "reason": "..."}
{"action": "done", "reason": "...", "data": {<optional structured data>}}
"""


def _compress_screenshot(screenshot_bytes: bytes) -> str:
    """Resize and compress a screenshot, return as base64 JPEG."""
    img = Image.open(io.BytesIO(screenshot_bytes))

    w, h = img.size
    if w > SCREENSHOT_MAX_WIDTH:
        ratio = SCREENSHOT_MAX_WIDTH / w
        img = img.resize((SCREENSHOT_MAX_WIDTH, int(h * ratio)), Image.LANCZOS)

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=SCREENSHOT_JPEG_QUALITY)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _get_client() -> OpenAI:
    return OpenAI(api_key=config.OPENAI_API_KEY)


def analyze_screen(
    screenshot_bytes: bytes,
    goal: str,
    context: str = "",
    viewport: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """
    Send a screenshot to the vision model and get back a structured action.

    The model returns coordinates in the compressed image space. We scale
    them back to the original browser viewport on our side so the model
    never has to do mental arithmetic.
    """
    img_b64 = _compress_screenshot(screenshot_bytes)

    # Compute the scale factor so we can map image-space coords -> viewport
    scale = 1.0
    img = Image.open(io.BytesIO(screenshot_bytes))
    orig_w = img.size[0]
    if orig_w > SCREENSHOT_MAX_WIDTH:
        scale = orig_w / SCREENSHOT_MAX_WIDTH

    user_prompt = f"CURRENT GOAL: {goal}"
    if context:
        user_prompt += f"\n\nCONTEXT: {context}"
    user_prompt += "\n\nLook at the screenshot and decide the next action."

    client = _get_client()

    try:
        response = client.chat.completions.create(
            model=VISION_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{img_b64}",
                                "detail": "high",
                            },
                        },
                    ],
                },
            ],
            max_completion_tokens=300,
            temperature=0.3,
        )

        raw = response.choices[0].message.content.strip()
        result = _parse_response(raw)

        # Scale coordinates from image-space back to browser viewport
        if scale != 1.0:
            for key in ("x", "y"):
                if key in result:
                    try:
                        result[key] = int(round(float(result[key]) * scale))
                    except (ValueError, TypeError):
                        pass

        return result

    except Exception as e:
        logger.error(f"Vision API error: {e}")
        return {"action": "wait", "reason": f"Vision API error: {e}"}


def _parse_response(raw: str) -> Dict[str, Any]:
    """Parse the model's JSON response, handling common formatting issues."""
    text = raw.strip()

    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        if start < 0:
            logger.warning(f"No JSON in vision response: {text[:200]}")
            return {"action": "wait", "reason": "No JSON in vision response"}
        try:
            decoder = json.JSONDecoder()
            result, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            logger.warning(f"Could not parse vision response: {text[:200]}")
            return {"action": "wait", "reason": "Could not parse vision response"}

    if "action" not in result:
        result["action"] = "wait"
        result["reason"] = "Missing action in response"

    # Ensure coordinates are integers
    for key in ("x", "y", "amount"):
        if key in result:
            try:
                result[key] = int(round(float(result[key])))
            except (ValueError, TypeError):
                pass

    return result


# ---------------------------------------------------------------------------
# Goal-specific prompt builders
# ---------------------------------------------------------------------------

def goal_dismiss_popup() -> str:
    return (
        "Look at the screen carefully. Is there a popup, cookie banner, overlay, "
        "or modal dialog blocking the page content? Common Airbnb popups include: "
        "'Now you'll see one price for your trip' with a 'Got it' button, or a "
        "cookie consent banner. If you see such a popup, click the dismiss button "
        "(look for 'Got it', 'Close', 'X', 'Accept', 'OK'). "
        "IMPORTANT: If there is NO popup or overlay visible and you can see the "
        "normal page content clearly, you MUST respond with done. Do NOT click on "
        "any navigation links, the Airbnb logo, or any listing cards."
    )


def goal_click_search_bar() -> str:
    return (
        "You are on the Airbnb homepage. At the top of the page there is a "
        "horizontal search bar with fields: 'Where' (or 'Search destinations'), "
        "'When' (or 'Add dates'), 'Who' (or 'Add guests'), and a red/pink "
        "search button on the right. Click directly on the 'Where' or "
        "'Search destinations' text/input field on the LEFT side of the search bar. "
        "Do NOT click on 'When', 'Who', or the search button."
    )


def goal_type_location(search_area: str) -> str:
    return (
        f"The 'Where' search input should now be focused (you can see a "
        f"'Suggested destinations' dropdown below it). Type the location: "
        f"{search_area}\n"
        "Use the 'type' action to enter this text. The input field is already "
        "focused so you can type directly -- do NOT click anything first."
    )


def goal_select_suggestion(search_area: str) -> str:
    return (
        f"You just typed '{search_area}' into the 'Where' search field. "
        "A dropdown of location suggestions should be visible below the "
        "search input. The suggestions show location names with small icons. "
        "Click on the first or best matching suggestion that corresponds to "
        f"'{search_area}'. If no dropdown is visible, respond with done."
    )


def goal_open_dates() -> str:
    return (
        "In the search bar at the top, click on the 'When' or 'Add dates' "
        "section to open the calendar/date picker. This is the middle section "
        "of the search bar, between 'Where' and 'Who'. It might also say "
        "'Check in' or show a date if one was already selected. "
        "If the calendar is already open/visible, respond with done."
    )


def goal_pick_date(target_date_str: str, label: str) -> str:
    return (
        f"The calendar/date picker is open. Find and click on the date "
        f"{target_date_str} ({label}). Airbnb's calendar shows two months "
        f"side by side. Look for the correct month name at the top of each "
        f"month grid, then find the day number. If the correct month is not "
        f"showing, click the right/forward arrow ('>') to navigate to the "
        f"right month. The days are shown as numbers in a grid. Click the "
        f"specific day number you need."
    )


def goal_click_search_button() -> str:
    return (
        "Click the search button to submit the search. It is the red/pink "
        "button with a magnifying glass icon and/or the word 'Search' at the "
        "RIGHT end of the search bar. It is typically a rounded rectangle. "
        "Click the center of this button."
    )


def goal_scan_results(target_name: str, target_id: str, page_num: int) -> str:
    return (
        f"You are on page {page_num} of Airbnb search results. "
        f"Look at all the listing cards visible on this page. "
        f"I am looking for a listing called '{target_name}' "
        f"(Airbnb listing ID: {target_id}). \n\n"
        "Count the listings from top to bottom. "
        "If you can see the target listing, respond with done and include "
        '{"found": true, "position": <position number>, "x": <click x>, "y": <click y>}. '
        "If the target is NOT on this page, respond with done and include "
        '{"found": false}. '
        "Scroll down first if you haven't seen the full page yet."
    )


def goal_click_next_page() -> str:
    return (
        "Scroll to the very bottom of the search results page. Look for "
        "pagination controls -- these are numbered page buttons (1, 2, 3...) "
        "and/or a 'Next' arrow button. Click the next page number or the "
        "'Next' / right arrow button ('>') to go to the next page of results. "
        "If you see no pagination controls or no 'Next' button, respond with "
        'done and include {"no_more_pages": true}.'
    )


def goal_engage_listing(listing_name: str, engagement_type: str) -> str:
    prompts = {
        "read_top": (
            f"You are viewing the listing page for '{listing_name}'. "
            "Scroll down slowly to read the title, rating, location info, "
            "host details, and the property summary at the top. A real guest "
            "would pause to read these details."
        ),
        "browse_photos": (
            "Look at the listing photos at the top of the page. Click on "
            "the main/hero photo or look for a 'Show all photos' button to "
            "view more images. Airbnb shows a photo grid with one large and "
            "several smaller photos."
        ),
        "close_photos": (
            "You are in the photo gallery/viewer. Look for a close button "
            "(X icon) or back arrow, usually in the top-left corner of the "
            "gallery overlay, to return to the listing page."
        ),
        "read_description": (
            "Scroll down to read the description section and the amenities "
            "list. Use the 'scroll' action to scroll down 300-400px."
        ),
        "check_calendar": (
            "Scroll down the listing page to find the calendar/availability "
            "section. It shows a grid of dates. Click on a few different "
            "dates to see pricing. Use the 'scroll' action to scroll down."
        ),
        "read_reviews": (
            "Scroll down to find the reviews section. It typically shows "
            "a star rating summary and individual guest reviews below. "
            "Use the 'scroll' action to scroll down."
        ),
        "scroll_general": (
            "Scroll down about 300-400px to continue exploring this listing "
            "page. Use the 'scroll' action with direction 'down'."
        ),
        "go_back": (
            "You are done viewing this listing. Use the 'key' action to press "
            "'Alt+ArrowLeft' or look for a back arrow (< or leftward arrow) "
            "in the top-left area of the page to go back to search results. "
            "If you see a left-pointing arrow or '<' near the top, click it."
        ),
    }
    return prompts.get(engagement_type, prompts["scroll_general"])


def goal_click_random_listing() -> str:
    return (
        "You are on the search results page. Pick any listing that looks "
        "interesting and click on it. Choose one that catches your eye, "
        "like a real guest would. Avoid clicking the very first listing."
    )


def goal_interact_with_map() -> str:
    return (
        "Look at the map on the right side of the search results. "
        "If the map is visible, click on it to interact. "
        "If no map is visible, respond with done."
    )
