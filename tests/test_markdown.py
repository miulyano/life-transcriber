from bot.utils.markdown import markdown_to_telegram_html


def test_escapes_html_special_chars():
    assert markdown_to_telegram_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"


def test_converts_bold():
    assert markdown_to_telegram_html("**жирный**") == "<b>жирный</b>"


def test_bold_inside_line():
    assert (
        markdown_to_telegram_html("категория **заголовок**: текст")
        == "категория <b>заголовок</b>: текст"
    )


def test_leading_star_becomes_bullet():
    assert markdown_to_telegram_html("* пункт один") == "• пункт один"


def test_leading_dash_becomes_bullet():
    assert markdown_to_telegram_html("- пункт") == "• пункт"


def test_triple_star_divider_line():
    result = markdown_to_telegram_html("Категория A\n***\nКатегория B")
    assert result == "Категория A\n———\nКатегория B"


def test_preserves_paragraphs():
    text = "**Тема 1:**\n* факт\n* ещё факт\n\n***\n\n**Тема 2:**\n* факт"
    expected = (
        "<b>Тема 1:</b>\n"
        "• факт\n"
        "• ещё факт\n"
        "\n"
        "———\n"
        "\n"
        "<b>Тема 2:</b>\n"
        "• факт"
    )
    assert markdown_to_telegram_html(text) == expected


def test_escape_then_bold_order():
    # `<` in content should be escaped; bold still works.
    assert (
        markdown_to_telegram_html("**a < b** ok")
        == "<b>a &lt; b</b> ok"
    )


def test_blank_line_inserted_after_glued_category_header():
    # Header ``Категория:`` glued to its content gets a blank line inserted so
    # the rendered summary visually separates the header from items.
    assert (
        markdown_to_telegram_html("Категория:\nтекст категории")
        == "Категория:\n\nтекст категории"
    )


def test_blank_line_not_inserted_when_already_present():
    assert (
        markdown_to_telegram_html("Категория:\n\nтекст")
        == "Категория:\n\nтекст"
    )


def test_blank_line_not_inserted_when_no_trailing_colon():
    assert (
        markdown_to_telegram_html("Просто строка\nещё строка")
        == "Просто строка\nещё строка"
    )
