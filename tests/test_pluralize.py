from bot.utils.pluralize import format_hm, plural_ru


def test_plural_ru_one():
    assert plural_ru(1, "час", "часа", "часов") == "час"
    assert plural_ru(21, "час", "часа", "часов") == "час"
    assert plural_ru(101, "час", "часа", "часов") == "час"


def test_plural_ru_few():
    assert plural_ru(2, "час", "часа", "часов") == "часа"
    assert plural_ru(3, "час", "часа", "часов") == "часа"
    assert plural_ru(4, "час", "часа", "часов") == "часа"
    assert plural_ru(22, "час", "часа", "часов") == "часа"
    assert plural_ru(104, "час", "часа", "часов") == "часа"


def test_plural_ru_many():
    assert plural_ru(0, "час", "часа", "часов") == "часов"
    assert plural_ru(5, "час", "часа", "часов") == "часов"
    assert plural_ru(11, "час", "часа", "часов") == "часов"
    assert plural_ru(12, "час", "часа", "часов") == "часов"
    assert plural_ru(14, "час", "часа", "часов") == "часов"
    assert plural_ru(15, "час", "часа", "часов") == "часов"
    assert plural_ru(20, "час", "часа", "часов") == "часов"
    assert plural_ru(25, "час", "часа", "часов") == "часов"
    assert plural_ru(111, "час", "часа", "часов") == "часов"


def test_plural_ru_minutes():
    assert plural_ru(1, "минута", "минуты", "минут") == "минута"
    assert plural_ru(2, "минута", "минуты", "минут") == "минуты"
    assert plural_ru(5, "минута", "минуты", "минут") == "минут"


def test_format_hm_zero():
    assert format_hm(0) == "0 минут"


def test_format_hm_only_minutes():
    assert format_hm(60 * 36) == "36 минут"
    assert format_hm(60 * 1) == "1 минута"
    assert format_hm(60 * 2) == "2 минуты"


def test_format_hm_only_hours():
    assert format_hm(3600) == "1 час"
    assert format_hm(3600 * 2) == "2 часа"
    assert format_hm(3600 * 5) == "5 часов"


def test_format_hm_hours_and_minutes():
    assert format_hm(3 * 3600 + 24 * 60) == "3 часа 24 минуты"
    assert format_hm(6 * 3600 + 36 * 60) == "6 часов 36 минут"
    assert format_hm(1 * 3600 + 1 * 60) == "1 час 1 минута"


def test_format_hm_rounds_to_minute():
    assert format_hm(29) == "0 минут"
    assert format_hm(30) == "1 минута"
    assert format_hm(89) == "1 минута"
    assert format_hm(90) == "2 минуты"


def test_format_hm_negative_clamped():
    assert format_hm(-100) == "0 минут"
