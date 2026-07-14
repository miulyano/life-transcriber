from unittest.mock import AsyncMock, MagicMock

import pytest

from aiogram.types import MessageEntity

from bot.handlers import links
from bot.handlers.links import URL_RE, extract_urls
from bot.services.user_facing_error import UserFacingError


def make_message(text=None, caption=None, entities=None, caption_entities=None):
    message = MagicMock()
    message.text = text
    message.caption = caption
    message.entities = entities
    message.caption_entities = caption_entities
    return message


@pytest.mark.parametrize("text,expected", [
    ("https://youtube.com/watch?v=abc", "https://youtube.com/watch?v=abc"),
    ("https://youtu.be/abc123", "https://youtu.be/abc123"),
    ("https://rutube.ru/video/xyz/", "https://rutube.ru/video/xyz/"),
    ("https://vk.com/video-123_456", "https://vk.com/video-123_456"),
    ("http://example.com", "http://example.com"),
    ("HTTPS://UPPERCASE.COM/path", "HTTPS://UPPERCASE.COM/path"),
])
def test_url_detected(text, expected):
    match = URL_RE.search(text)
    assert match is not None
    assert match.group(0) == expected


def test_url_extracted_from_surrounding_text():
    match = URL_RE.search("Смотри это видео https://youtu.be/abc круто!")
    assert match is not None
    # URL_RE is greedy on \S+ — it captures until whitespace, so trailing ! may be included
    assert match.group(0).startswith("https://youtu.be/abc")


@pytest.mark.parametrize("text", [
    "просто текст без ссылки",
    "",
    "example.com",  # no scheme
    "ftp://example.com",  # unsupported scheme
    "file:///etc/passwd",  # unsupported scheme
])
def test_no_url(text):
    assert URL_RE.search(text) is None


def test_findall_multiple_urls():
    text = "первая https://youtu.be/a и вторая https://vk.com/video123"
    urls = URL_RE.findall(text)
    assert len(urls) == 2


def test_friendly_error_yandex_music():
    text = links._friendly_error(
        "yandex-music: пришлите ссылку на конкретный выпуск подкаста"
    )
    assert text == "Пришлите ссылку на конкретный выпуск подкаста"


def test_friendly_error_accepts_typed_provider_error():
    text = links._friendly_error(UserFacingError("facebook", "не удалось скачать видео"))
    assert text == "Не удалось скачать видео"


def test_extract_urls_from_plain_text():
    message = make_message(text="первая https://youtu.be/a и вторая https://vk.com/video123 и снова https://youtu.be/a")
    assert extract_urls(message) == ["https://youtu.be/a", "https://vk.com/video123"]


def test_extract_urls_from_caption():
    message = make_message(caption="Смотри https://youtu.be/x")
    assert extract_urls(message) == ["https://youtu.be/x"]


def test_extract_urls_text_link_entity():
    # Гиперссылка: URL живёт в entity.url, в видимом тексте его нет
    message = make_message(
        text="Смотри видео",
        entities=[MessageEntity(type="text_link", offset=0, length=6, url="https://youtu.be/x")],
    )
    assert extract_urls(message) == ["https://youtu.be/x"]


def test_extract_urls_text_link_entity_in_caption():
    message = make_message(
        caption="Пост с гиперссылкой",
        caption_entities=[MessageEntity(type="text_link", offset=0, length=4, url="https://youtu.be/c")],
    )
    assert extract_urls(message) == ["https://youtu.be/c"]


def test_extract_urls_entity_offsets_utf16():
    # Эмодзи = 2 UTF-16 code units; entity даёт точные границы без хвостового «!»
    text = "😀😀 https://youtu.be/x!"
    message = make_message(
        text=text,
        entities=[MessageEntity(type="url", offset=5, length=18)],
    )
    assert extract_urls(message)[0] == "https://youtu.be/x"


def test_extract_urls_entity_beats_regex_tail():
    # Entity идёт раньше regex-находки, поэтому urls[0] — чистый URL без «!»
    text = "Смотри https://youtu.be/x!"
    message = make_message(
        text=text,
        entities=[MessageEntity(type="url", offset=7, length=18)],
    )
    urls = extract_urls(message)
    assert urls[0] == "https://youtu.be/x"


def test_extract_urls_schemeless_entity_ignored():
    # Telegram размечает example.com как url-entity, но без схемы бот его не берёт
    message = make_message(
        text="зайди на example.com",
        entities=[MessageEntity(type="url", offset=9, length=11)],
    )
    assert extract_urls(message) == []


def test_contains_url_filter_negative():
    message = make_message(text="просто текст", caption=None)
    assert links._contains_url(message) is False


async def test_handle_link_asks_timecode_choice(monkeypatch):
    registered = []

    def fake_put_job(job):
        registered.append(job)
        return "pid123"

    from bot.handlers import _timecode_prompt

    monkeypatch.setattr(_timecode_prompt, "put_job", fake_put_job)

    message = make_message(text="https://example.com/video")
    message.from_user.id = 777
    message.reply = AsyncMock()

    await links.handle_link(message)

    assert len(registered) == 1
    job = registered[0]
    assert job.kind == "link"
    assert job.url == "https://example.com/video"
    assert job.user_id == 777
    assert job.source_type == "link"  # non-platform URL falls back to generic "link"
    message.reply.assert_awaited_once()
    keyboard = message.reply.await_args.kwargs["reply_markup"]
    callback_datas = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert callback_datas == ["tc:1:pid123", "tc:0:pid123", "tc:x:pid123"]


async def test_handle_link_from_caption(monkeypatch):
    """Пересланный пост с медиа: URL в caption, message.text = None."""
    registered = []

    def fake_put_job(job):
        registered.append(job)
        return "pid456"

    from bot.handlers import _timecode_prompt

    monkeypatch.setattr(_timecode_prompt, "put_job", fake_put_job)

    message = make_message(caption="Смотри https://youtu.be/abc")
    message.from_user.id = 777
    message.reply = AsyncMock()

    await links.handle_link(message)

    assert len(registered) == 1
    job = registered[0]
    assert job.kind == "link"
    assert job.url == "https://youtu.be/abc"
    message.reply.assert_awaited_once()


async def test_process_link_keeps_progress_until_result_is_sent(tmp_path, monkeypatch):
    events = []
    audio_path = tmp_path / "audio.mp3"

    class Reporter:
        def __init__(self, _message, label, **_kwargs):
            events.append(("init", label))

        async def __aenter__(self):
            events.append("enter")
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            events.append("exit")

        async def set_phase(self, label):
            events.append(("phase", label))

        async def set_progress(self, _current, _total):
            events.append("progress")

        async def set_progress_fraction(self, _fraction):
            events.append("fraction")

        async def finish(self):
            events.append("finish")

        async def fail(self, text):
            events.append(("fail", text))

    async def fake_download_audio(url, _output_dir, **_kwargs):
        events.append(("download", url))
        audio_path.write_bytes(b"audio")
        from bot.services.source_meta import SourceMetadata as _SM
        return str(audio_path), _SM()

    async def fake_pipeline(audio_path, *, reporter, deliver_text, user_id, source_meta=None, on_phase_change=None, source_type="unknown", transcript_store=None):
        events.append(("pipeline", audio_path, user_id, source_meta))
        await reporter.set_phase("Форматирую…")
        await reporter.set_phase("Отправляю результат…")
        await deliver_text("transcript")

    async def fake_reply_text_or_file(_message, text, file_text=None, *, source_type=None):
        events.append(("reply", text))

    monkeypatch.setattr(links, "ProgressReporter", Reporter)
    monkeypatch.setattr(links, "download_audio", fake_download_audio)
    monkeypatch.setattr(links, "run_transcription_pipeline", fake_pipeline)
    monkeypatch.setattr(links, "reply_text_or_file", fake_reply_text_or_file)

    message = MagicMock()
    message.text = "https://example.com/video"
    message.from_user.id = 777

    await links.process_link(message, "https://example.com/video")

    assert events.index(("phase", "Отправляю результат…")) < events.index(("reply", "transcript"))
    assert events.index(("reply", "transcript")) < events.index("finish")


async def test_process_link_logs_download_error(tmp_path, monkeypatch, caplog):
    """A yt-dlp failure is swallowed into a friendly message; the real error
    text must still reach the logs so transient failures are diagnosable."""
    import logging

    failed = []

    class Reporter:
        def __init__(self, _message, label, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def set_phase(self, label):
            pass

        async def set_progress_fraction(self, _fraction):
            pass

        async def fail(self, text):
            failed.append(text)

    async def fake_download_audio(url, _output_dir, **_kwargs):
        raise RuntimeError("yt-dlp failed (code 1): ERROR: [youtube] SABR streaming")

    monkeypatch.setattr(links, "ProgressReporter", Reporter)
    monkeypatch.setattr(links, "download_audio", fake_download_audio)

    message = MagicMock()
    message.from_user.id = 777

    with caplog.at_level(logging.WARNING, logger="bot.handlers.links"):
        await links.process_link(message, "https://example.com/video")

    # User still gets the friendly message…
    assert failed and "Не удалось скачать" in failed[0]
    # …and the raw yt-dlp error is logged for diagnosis.
    assert any("SABR streaming" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_process_link_logs_download_error_cause_chain(
    tmp_path, monkeypatch, caplog
):
    """The friendly UserFacingError hides the real cause set via `raise ... from`.
    The underlying cause text (e.g. a yt-dlp TypeError) must still reach the logs,
    not just the friendly detail — otherwise transient failures are undiagnosable."""
    import logging

    failed = []

    class Reporter:
        def __init__(self, _message, label, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def set_phase(self, label):
            pass

        async def set_progress_fraction(self, _fraction):
            pass

        async def fail(self, text):
            failed.append(text)

    async def fake_download_audio(url, _output_dir, **_kwargs):
        try:
            raise RuntimeError("argument of type 'bool' is not iterable")
        except RuntimeError as real_cause:
            raise UserFacingError(
                "yandex-music", "не удалось скачать выпуск"
            ) from real_cause

    monkeypatch.setattr(links, "ProgressReporter", Reporter)
    monkeypatch.setattr(links, "download_audio", fake_download_audio)

    message = MagicMock()
    message.from_user.id = 777

    with caplog.at_level(logging.WARNING, logger="bot.handlers.links"):
        await links.process_link(message, "https://music.yandex.ru/album/1/track/2")

    # User gets the friendly message…
    assert failed and "Не удалось скачать выпуск" in failed[0]
    # …and the hidden root cause is logged for diagnosis.
    assert any("bool' is not iterable" in r.getMessage() for r in caplog.records)


async def test_process_link_queued_label_when_semaphore_busy(monkeypatch):
    """Пока все слоты семафора заняты, статус-сообщение — «В очереди…»."""
    import asyncio

    from bot.services import task_registry

    monkeypatch.setattr(task_registry.settings, "MAX_CONCURRENT_TRANSCRIPTIONS", 1)
    sem = task_registry.get_semaphore()
    await sem.acquire()  # слот занят «первой» задачей

    labels = []

    class Reporter:
        def __init__(self, _message, label, **_kwargs):
            labels.append(label)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def set_phase(self, label):
            labels.append(label)

        async def set_progress_fraction(self, _fraction):
            pass

        async def finish(self):
            pass

    async def fake_download_audio(url, _output_dir, **_kwargs):
        from bot.services.source_meta import SourceMetadata as _SM

        return "/nonexistent/audio.mp3", _SM()

    async def fake_pipeline(*_args, **_kwargs):
        pass

    monkeypatch.setattr(links, "ProgressReporter", Reporter)
    monkeypatch.setattr(links, "download_audio", fake_download_audio)
    monkeypatch.setattr(links, "run_transcription_pipeline", fake_pipeline)

    message = MagicMock()
    message.from_user.id = 777

    task = asyncio.create_task(links.process_link(message, "https://example.com/v"))
    await asyncio.sleep(0)  # репортер создан, задача ждёт слот
    assert labels == ["В очереди…"]

    sem.release()
    await task
    assert labels[1] == "Скачиваю аудио по ссылке…"


async def test_process_link_cancelled_cleans_temp_and_propagates(tmp_path, monkeypatch):
    """Отмена во время транскрибации: temp-файл удалён, CancelledError наружу."""
    import asyncio

    audio_path = tmp_path / "audio.mp3"

    class Reporter:
        def __init__(self, _message, label, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None  # CancelledError пропагирует, как в настоящем репортере

        async def set_phase(self, label):
            pass

        async def set_progress_fraction(self, _fraction):
            pass

    async def fake_download_audio(url, _output_dir, **_kwargs):
        from bot.services.source_meta import SourceMetadata as _SM

        audio_path.write_bytes(b"audio")
        return str(audio_path), _SM()

    async def fake_pipeline(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(links, "ProgressReporter", Reporter)
    monkeypatch.setattr(links, "download_audio", fake_download_audio)
    monkeypatch.setattr(links, "run_transcription_pipeline", fake_pipeline)

    message = MagicMock()
    message.from_user.id = 777

    with pytest.raises(asyncio.CancelledError):
        await links.process_link(message, "https://example.com/v")

    assert not audio_path.exists()
