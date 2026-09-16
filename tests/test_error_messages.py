from bot.services.error_messages import format_download_error


YTDLP_AGE_ERROR = (
    "yt-dlp failed (code 1): ERROR: [youtube] rNaKQyr-4ig: Sign in to confirm "
    "your age. Use --cookies-from-browser or --cookies for the authentication. "
    "See https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp"
)


def test_age_restricted_youtube_gets_specific_message():
    text = format_download_error(RuntimeError(YTDLP_AGE_ERROR))
    assert "возрастн" in text.lower()
    assert "Не удалось скачать видео с этой платформы" not in text


def test_other_ytdlp_error_keeps_generic_message():
    text = format_download_error(
        RuntimeError("yt-dlp failed (code 1): ERROR: [youtube] abc: SABR streaming")
    )
    assert text == "Не удалось скачать видео с этой платформы. Попробуй другую ссылку."
