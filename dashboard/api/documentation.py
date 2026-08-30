"""Render the canonical API Markdown as a safe public documentation page."""

from __future__ import annotations

import html
import re
from pathlib import Path


API_DOCUMENT_PATH = Path(__file__).resolve().parents[2] / "docs" / "api.md"
HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
LIST_RE = re.compile(r"^\s*-\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^```([A-Za-z0-9_-]*)\s*$")
INLINE_CODE_RE = re.compile(r"`([^`]+)`")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
NON_SLUG_RE = re.compile(r"[^a-z0-9]+")


def read_api_markdown() -> str:
    """Read the single source of truth used by HTML and agent consumers."""
    return API_DOCUMENT_PATH.read_text(encoding="utf-8")


def render_api_markdown(markdown_text: str) -> tuple[str, list[dict]]:
    """Render the controlled API document without allowing raw HTML through."""
    output: list[str] = []
    toc: list[dict] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    code_lines: list[str] = []
    in_code = False
    code_language = ""
    used_anchors: dict[str, int] = {}

    def flush_paragraph() -> None:
        if paragraph:
            output.append(f"<p>{_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        if list_items:
            output.append("<ul>" + "".join(
                f"<li>{_inline(item)}</li>" for item in list_items
            ) + "</ul>")
            list_items.clear()

    for raw_line in markdown_text.splitlines():
        fence = FENCE_RE.match(raw_line)
        if fence:
            if in_code:
                language_class = (
                    f' class="language-{html.escape(code_language, quote=True)}"'
                    if code_language else ""
                )
                output.append(
                    f"<pre><code{language_class}>"
                    f"{html.escape(chr(10).join(code_lines))}</code></pre>"
                )
                code_lines.clear()
                in_code = False
                code_language = ""
            else:
                flush_paragraph()
                flush_list()
                in_code = True
                code_language = fence.group(1)
            continue

        if in_code:
            code_lines.append(raw_line)
            continue

        heading = HEADING_RE.match(raw_line)
        if heading:
            flush_paragraph()
            flush_list()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            anchor = _unique_anchor(title, used_anchors)
            output.append(
                f'<h{level} id="{anchor}">{_inline(title)}'
                f'<a class="heading-anchor" href="#{anchor}" aria-label="Link to this section">#</a>'
                f"</h{level}>"
            )
            if level >= 2:
                toc.append({"level": level, "title": title, "anchor": anchor})
            continue

        list_match = LIST_RE.match(raw_line)
        if list_match:
            flush_paragraph()
            list_items.append(list_match.group(1))
            continue

        if not raw_line.strip():
            flush_paragraph()
            flush_list()
            continue

        if list_items:
            # Wrapped Markdown list lines belong to the preceding item.
            list_items[-1] += " " + raw_line.strip()
        else:
            paragraph.append(raw_line.strip())

    if in_code:
        output.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    flush_paragraph()
    flush_list()
    return "\n".join(output), toc


def _inline(value: str) -> str:
    escaped = html.escape(value, quote=True)
    escaped = INLINE_CODE_RE.sub(r"<code>\1</code>", escaped)
    escaped = BOLD_RE.sub(r"<strong>\1</strong>", escaped)
    return escaped


def _unique_anchor(title: str, used: dict[str, int]) -> str:
    base = NON_SLUG_RE.sub("-", title.lower()).strip("-") or "section"
    used[base] = used.get(base, 0) + 1
    return base if used[base] == 1 else f"{base}-{used[base]}"
