from bot.utils.text_chunking import SENTENCE_BOUNDARIES, split_long_text


def test_empty_text_returns_empty_list():
    assert split_long_text("", max_chars=100) == []
    assert split_long_text("   ", max_chars=100) == []


def test_short_text_returns_single_chunk():
    assert split_long_text("hello world", max_chars=100) == ["hello world"]


def test_split_long_text_adds_overlap():
    text = "\n\n".join(
        [
            " ".join(f"alpha{i}" for i in range(20)),
            " ".join(f"bravo{i}" for i in range(20)),
            " ".join(f"charlie{i}" for i in range(20)),
        ]
    )

    chunks = split_long_text(text, max_chars=180, overlap_chars=30)

    assert len(chunks) > 1
    assert all(len(chunk) <= 180 for chunk in chunks)
    assert chunks[1].startswith(chunks[0][-30:].strip())


def test_overlap_zero_disables_overlap():
    # With overlap_chars=0 there must be NO duplication between neighbors.
    paragraphs = ["".join(chr(ord("a") + (i % 26)) for _ in range(90)) for i in range(5)]
    text = "\n\n".join(paragraphs)

    chunks = split_long_text(text, max_chars=200, overlap_chars=0)

    assert len(chunks) > 1
    # Sum of chunk lengths should not massively exceed original (no duplication).
    total_chars = sum(len(c) for c in chunks)
    assert total_chars <= len(text) + 10  # tiny allowance for trimming


def test_oversized_word_is_hard_split():
    # A single word longer than the budget must still be split, not dropped.
    long_word = "x" * 250
    chunks = split_long_text(long_word, max_chars=100, overlap_chars=0)
    assert len(chunks) >= 3
    assert "".join(chunks) == long_word


def test_sentence_boundaries_avoid_mid_phrase_cut():
    # Build a long paragraph of full sentences.
    sentence = "Это длинное предложение на русском языке."
    text = " ".join([sentence] * 30)

    chunks = split_long_text(
        text,
        max_chars=200,
        overlap_chars=0,
        prefer_boundaries=SENTENCE_BOUNDARIES,
    )

    assert len(chunks) > 1
    # Every chunk except possibly the last one must end with a sentence-ending
    # punctuation mark — i.e., we did not cut mid-phrase.
    for chunk in chunks[:-1]:
        assert chunk.rstrip().endswith((".", "!", "?", "…")), chunk


def test_sentence_boundary_falls_back_to_words_when_no_punct():
    # One giant word-only paragraph: no sentence boundaries exist. Must still
    # split without error.
    text = " ".join([f"word{i}" for i in range(400)])

    chunks = split_long_text(
        text,
        max_chars=200,
        overlap_chars=0,
        prefer_boundaries=SENTENCE_BOUNDARIES,
    )

    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)
