"""Convert lightweight Markdown from GPT summaries into Telegram-safe HTML.

GPT models keep inserting `**bold**`, bullet `*`, and `***`-style dividers
around category headers even when told not to. Telegram renders HTML, not
Markdown, so we post-process the summary here: escape HTML, convert
`**text**` → `<b>text</b>`, turn leading bullet `*` into `•`, and keep `***`
lines as category separators.
"""

from __future__ import annotations

import html
import re

BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
BULLET_LINE_RE = re.compile(r"^\s*[\*\-]\s+")
DIVIDER_RE = re.compile(r"^\s*\*{3,}\s*$")


def markdown_to_telegram_html(text: str) -> str:
    """Return HTML safe for Telegram's HTML parse_mode.

    - Escapes `<`, `>`, `&` in user text.
    - `**bold**` → `<b>bold</b>` (non-greedy, single level).
    - Leading `* ` or `- ` on a line becomes `• `.
    - Lines of three or more `*` become a `———` divider between categories.
    """
    lines = text.split("\n")
    out: list[str] = []
    for line in lines:
        if DIVIDER_RE.match(line):
            out.append("———")
            continue
        stripped = BULLET_LINE_RE.sub("• ", line)
        escaped = html.escape(stripped, quote=False)
        escaped = BOLD_RE.sub(r"<b>\1</b>", escaped)
        out.append(escaped)
    return "\n".join(out)
