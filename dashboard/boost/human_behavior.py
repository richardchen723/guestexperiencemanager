#!/usr/bin/env python3
"""
Human-like browser behavior using only raw IO (mouse, keyboard, scroll).

No DOM selectors, no locators, no page.evaluate -- just pixel coordinates
and keyboard input, indistinguishable from a real person.
"""

import asyncio
import math
import random
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

async def random_delay(min_s: float = 0.5, max_s: float = 2.0):
    """Sleep for a random duration. ~15% chance of an extra 'thinking' pause."""
    delay = random.uniform(min_s, max_s)
    if random.random() < 0.15:
        delay += random.uniform(1.5, 4.0)
    await asyncio.sleep(delay)


async def reading_pause(text_length: int = 200):
    """Pause proportional to content length, like a human reading."""
    words = max(20, text_length // 5)
    seconds = words / random.uniform(200, 350) * 60
    seconds = min(seconds, 12)
    seconds = max(seconds, 1.5)
    await asyncio.sleep(seconds)


# ---------------------------------------------------------------------------
# Mouse movement with Bezier curves
# ---------------------------------------------------------------------------

def _bezier_points(
    start: Tuple[float, float],
    end: Tuple[float, float],
    num_points: int = 20,
) -> List[Tuple[int, int]]:
    """
    Generate points along a cubic Bezier curve between start and end
    with two random control points, producing a natural arc.
    """
    sx, sy = start
    ex, ey = end
    dx = ex - sx
    dy = ey - sy
    dist = math.hypot(dx, dy)

    # Control points: offset from the straight line by a random amount
    spread = max(30, dist * 0.3)
    c1x = sx + dx * random.uniform(0.2, 0.4) + random.uniform(-spread, spread)
    c1y = sy + dy * random.uniform(0.2, 0.4) + random.uniform(-spread, spread)
    c2x = sx + dx * random.uniform(0.6, 0.8) + random.uniform(-spread, spread)
    c2y = sy + dy * random.uniform(0.6, 0.8) + random.uniform(-spread, spread)

    points = []
    for i in range(num_points + 1):
        t = i / num_points
        u = 1 - t
        x = u**3 * sx + 3 * u**2 * t * c1x + 3 * u * t**2 * c2x + t**3 * ex
        y = u**3 * sy + 3 * u**2 * t * c1y + 3 * u * t**2 * c2y + t**3 * ey
        points.append((int(round(x)), int(round(y))))
    return points


async def smooth_mouse_move(page, x: int, y: int):
    """Move the mouse from its current position to (x, y) along a Bezier curve."""
    # Get current mouse position (approximate from viewport center if unknown)
    vw = page.viewport_size["width"]
    vh = page.viewport_size["height"]
    # Start from a plausible position -- we track via previous moves in practice
    # but as a safe default use viewport center
    start_x = getattr(page, "_last_mouse_x", vw // 2)
    start_y = getattr(page, "_last_mouse_y", vh // 2)

    num_steps = random.randint(15, 30)
    points = _bezier_points((start_x, start_y), (x, y), num_steps)

    for px, py in points:
        await page.mouse.move(px, py)
        await asyncio.sleep(random.uniform(0.003, 0.015))

    # Store final position
    page._last_mouse_x = x
    page._last_mouse_y = y


async def human_click(page, x: int, y: int, button: str = "left"):
    """Move to (x, y) with a natural curve, add a small random offset, then click."""
    offset_x = random.randint(-3, 3)
    offset_y = random.randint(-3, 3)
    target_x = max(0, x + offset_x)
    target_y = max(0, y + offset_y)

    await smooth_mouse_move(page, target_x, target_y)
    await asyncio.sleep(random.uniform(0.05, 0.15))
    await page.mouse.click(target_x, target_y, button=button)
    await asyncio.sleep(random.uniform(0.1, 0.3))


async def human_double_click(page, x: int, y: int):
    """Double-click at (x, y) with natural movement."""
    await smooth_mouse_move(page, x, y)
    await asyncio.sleep(random.uniform(0.05, 0.12))
    await page.mouse.dblclick(x, y)
    await asyncio.sleep(random.uniform(0.1, 0.3))


# ---------------------------------------------------------------------------
# Keyboard
# ---------------------------------------------------------------------------

async def human_type(page, text: str):
    """Type text character by character with variable delays."""
    for i, char in enumerate(text):
        delay_ms = random.randint(40, 160)
        await page.keyboard.type(char, delay=delay_ms)

        # Occasional micro-pause (simulates thinking mid-word)
        if random.random() < 0.08:
            await asyncio.sleep(random.uniform(0.2, 0.6))

        # Rare longer pause (simulates distraction)
        if random.random() < 0.03:
            await asyncio.sleep(random.uniform(0.5, 1.5))


async def human_press_key(page, key: str):
    """Press a key with a small delay before and after."""
    await asyncio.sleep(random.uniform(0.05, 0.2))
    await page.keyboard.press(key)
    await asyncio.sleep(random.uniform(0.1, 0.3))


# ---------------------------------------------------------------------------
# Scrolling
# ---------------------------------------------------------------------------

async def human_scroll(page, direction: str = "down", distance: int = 0):
    """Scroll in chunks with variable speed and occasional mid-scroll pauses."""
    if distance == 0:
        distance = random.randint(200, 500)

    delta = distance if direction == "down" else -distance
    chunks = random.randint(2, 4)
    per_chunk = delta / chunks

    for i in range(chunks):
        await page.mouse.wheel(0, per_chunk)
        await asyncio.sleep(random.uniform(0.08, 0.3))
        if random.random() < 0.25 and i < chunks - 1:
            await asyncio.sleep(random.uniform(0.4, 1.2))


async def scroll_to_bottom_slowly(page, max_scrolls: int = 12):
    """Scroll down the page in human-like increments."""
    for _ in range(max_scrolls):
        await human_scroll(page, "down", random.randint(250, 450))
        await random_delay(0.8, 2.5)


# ---------------------------------------------------------------------------
# Mouse idle / wander
# ---------------------------------------------------------------------------

async def mouse_wander(page):
    """Move the mouse randomly within the viewport to simulate idle cursor."""
    vw = page.viewport_size["width"]
    vh = page.viewport_size["height"]

    steps = random.randint(2, 4)
    for _ in range(steps):
        x = random.randint(100, vw - 100)
        y = random.randint(100, vh - 100)
        await smooth_mouse_move(page, x, y)
        await asyncio.sleep(random.uniform(0.3, 1.0))


# ---------------------------------------------------------------------------
# Map interaction
# ---------------------------------------------------------------------------

async def map_zoom(page, map_center_x: int, map_center_y: int, zoom_in: bool = True):
    """Position cursor over the map area, then scroll-wheel to zoom in or out."""
    await smooth_mouse_move(page, map_center_x, map_center_y)
    await asyncio.sleep(random.uniform(0.3, 0.8))

    scroll_amount = random.randint(150, 350)
    delta = -scroll_amount if zoom_in else scroll_amount

    ticks = random.randint(2, 4)
    for _ in range(ticks):
        await page.mouse.wheel(0, delta // ticks)
        await asyncio.sleep(random.uniform(0.15, 0.4))

    await asyncio.sleep(random.uniform(0.5, 1.5))


async def map_drag(page, start_x: int, start_y: int, end_x: int, end_y: int):
    """Click-and-drag on the map to pan it."""
    await smooth_mouse_move(page, start_x, start_y)
    await asyncio.sleep(random.uniform(0.1, 0.3))
    await page.mouse.down()
    await asyncio.sleep(random.uniform(0.05, 0.15))

    # Drag in small steps
    steps = random.randint(10, 20)
    for i in range(steps + 1):
        t = i / steps
        cx = int(start_x + (end_x - start_x) * t)
        cy = int(start_y + (end_y - start_y) * t)
        await page.mouse.move(cx, cy)
        await asyncio.sleep(random.uniform(0.01, 0.03))

    await page.mouse.up()
    await asyncio.sleep(random.uniform(0.3, 0.8))

    page._last_mouse_x = end_x
    page._last_mouse_y = end_y


# ---------------------------------------------------------------------------
# Random viewport / user-agent / locale / timezone
# ---------------------------------------------------------------------------

COMMON_VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1366, "height": 768},
    {"width": 1280, "height": 720},
    {"width": 1600, "height": 900},
    {"width": 1680, "height": 1050},
]

COMMON_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:133.0) Gecko/20100101 Firefox/133.0",
]

COMMON_LOCALES = ["en-US", "en-GB", "en-CA", "en-AU"]
COMMON_TIMEZONES = [
    "America/New_York", "America/Chicago", "America/Denver",
    "America/Los_Angeles", "America/Phoenix", "America/Detroit",
]


def get_random_viewport() -> dict:
    return random.choice(COMMON_VIEWPORTS).copy()


def get_random_user_agent() -> str:
    return random.choice(COMMON_USER_AGENTS)


def get_random_locale() -> str:
    return random.choice(COMMON_LOCALES)


def get_random_timezone() -> str:
    return random.choice(COMMON_TIMEZONES)
