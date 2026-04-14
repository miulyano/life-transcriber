import pytest

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
