"""Shared text chunking utility.

Used by summarizer (paragraph-aware chunking with overlap) and formatter
(sentence-aware chunking without overlap). Original implementation was
``_split_long_text`` inside ``bot.services.summarizer``.
"""

from __future__ import annotations

# Sentence-ish boundaries useful for the formatter to avoid cutting mid-phrase.
# Order within the tuple does not matter — we pick the latest occurrence.
SENTENCE_BOUNDARIES: tuple[str, ...] = (
    ". ",
    "! ",
    "? ",
    "… ",
    ".\n",
    "!\n",
    "?\n",
    "…\n",
)


def split_long_text(
    text: str,
    max_chars: int,
    overlap_chars: int = 0,
    prefer_boundaries: tuple[str, ...] = (),
) -> list[str]:
    """Split ``text`` into chunks no larger than ``max_chars`` characters.

    Paragraphs (separated by a blank line) are kept intact when possible.
    Paragraphs that exceed the char budget are split further:

    * If ``prefer_boundaries`` is non-empty, the splitter prefers cutting at
      the latest occurrence of one of those markers within the window
      (e.g. ``". "`` for sentence-aware formatting).
    * Otherwise — and as a fallback when no preferred boundary fits — it
      falls back to a word-boundary split. Words longer than the budget
      are hard-chopped.

    When ``overlap_chars > 0``, every chunk after the first gets the tail
    of the previous chunk prepended (useful for summarizer context).
    ``overlap_chars=0`` disables overlap entirely (the formatter needs
    clean, non-duplicated boundaries).
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    # Reserve overlap budget inside each chunk so that after prepending the
    # tail from the previous chunk we still fit within ``max_chars``.
    content_limit = max(1, max_chars - overlap_chars - 2)

    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    chunks: list[str] = []
    current = ""

    def append_piece(piece: str) -> None:
        nonlocal current
        if not current:
            current = piece
            return
        candidate = f"{current}\n\n{piece}"
        if len(candidate) <= content_limit:
            current = candidate
            return
        chunks.append(current)
        current = piece

    for paragraph in paragraphs:
        if len(paragraph) <= content_limit:
            append_piece(paragraph)
            continue

        if prefer_boundaries:
            pieces = _split_on_boundaries(paragraph, content_limit, prefer_boundaries)
        else:
            pieces = _split_on_words(paragraph, content_limit)
        for piece in pieces:
            append_piece(piece)

    if current:
        chunks.append(current)

    if overlap_chars <= 0 or len(chunks) <= 1:
        return chunks

    chunks_with_overlap = [chunks[0]]
    for previous, chunk in zip(chunks, chunks[1:]):
        overlap = previous[-overlap_chars:].strip()
        if overlap:
            chunks_with_overlap.append(f"{overlap}\n\n{chunk}")
        else:
            chunks_with_overlap.append(chunk)
    return chunks_with_overlap


def _split_on_words(text: str, content_limit: int) -> list[str]:
    words = text.split()
    pieces: list[str] = []
    piece = ""
    for word in words:
        if len(word) > content_limit:
            if piece:
                pieces.append(piece)
                piece = ""
            for start in range(0, len(word), content_limit):
                pieces.append(word[start : start + content_limit])
            continue
        candidate = f"{piece} {word}".strip()
        if len(candidate) <= content_limit:
            piece = candidate
            continue
        if piece:
            pieces.append(piece)
        piece = word
    if piece:
        pieces.append(piece)
    return pieces


def _split_on_boundaries(
    text: str, content_limit: int, boundaries: tuple[str, ...]
) -> list[str]:
    pieces: list[str] = []
    remaining = text
    while len(remaining) > content_limit:
        window = remaining[:content_limit]
        best_end = -1  # position after the boundary inside ``window``
        for boundary in boundaries:
            idx = window.rfind(boundary)
            if idx == -1:
                continue
            end = idx + len(boundary)
            if end > best_end:
                best_end = end
        if best_end == -1:
            # No preferred boundary fits — fall back to word split for this
            # window. We only consume the window chunk; the rest stays in
            # ``remaining`` so the next iteration can again look for a
            # boundary.
            pieces.extend(_split_on_words(window, content_limit))
            remaining = remaining[content_limit:].lstrip()
        else:
            pieces.append(remaining[:best_end].rstrip())
            remaining = remaining[best_end:].lstrip()
    if remaining:
        pieces.append(remaining)
    return pieces
