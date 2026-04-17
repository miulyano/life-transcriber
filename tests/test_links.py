from unittest.mock import MagicMock

import pytest

from bot.handlers import links
from bot.handlers.links import URL_RE


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


async def test_handle_link_keeps_progress_until_result_is_sent(tmp_path, monkeypatch):
    events = []
    audio_path = tmp_path / "audio.mp3"

    class Reporter:
        def __init__(self, _message, label):
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

    async def fake_download_audio(url, _output_dir):
        events.append(("download", url))
        audio_path.write_bytes(b"audio")
        return str(audio_path)

    async def fake_transcribe(path, **_kwargs):
        events.append(("transcribe", path))
        return "transcript"

    async def fake_reply_text_or_file(_message, text):
        events.append(("reply", text))

    monkeypatch.setattr(links, "ProgressReporter", Reporter)
    monkeypatch.setattr(links, "download_audio", fake_download_audio)
    monkeypatch.setattr(links, "transcribe", fake_transcribe)
    monkeypatch.setattr(links, "reply_text_or_file", fake_reply_text_or_file)

    message = MagicMock()
    message.text = "https://example.com/video"

    await links.handle_link(message)

    assert events.index(("phase", "Отправляю результат…")) < events.index(("reply", "transcript"))
    assert events.index(("reply", "transcript")) < events.index("finish")
