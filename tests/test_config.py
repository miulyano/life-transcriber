from bot.config import Settings


def _make(user_ids: str) -> Settings:
    return Settings(BOT_TOKEN="t", OPENAI_API_KEY="k", ALLOWED_USER_IDS=user_ids)


def test_multiple_user_ids():
    assert _make("111,222,333").allowed_user_ids == [111, 222, 333]


def test_single_user_id():
    assert _make("555").allowed_user_ids == [555]


def test_user_ids_with_spaces():
    assert _make("111, 222 , 333").allowed_user_ids == [111, 222, 333]


def test_empty_entries_skipped():
    assert _make("111,,222").allowed_user_ids == [111, 222]


def test_trailing_comma_skipped():
    assert _make("111,222,").allowed_user_ids == [111, 222]


def test_defaults():
    s = _make("111")
    assert s.LONG_TEXT_THRESHOLD == 2000
    assert s.WHISPER_MODEL == "whisper-1"
    assert s.GPT_MODEL == "gpt-4o"
    assert s.TEMP_DIR == "/tmp/transcriber"
    assert s.COBALT_API_URL == "http://cobalt:9000"
