from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import webapp.main as webapp_main


def _mock_bot(message_id: int = 10):
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=SimpleNamespace(message_id=message_id))
    bot.edit_message_text = AsyncMock()
    bot.delete_message = AsyncMock()
    bot.session = SimpleNamespace(close=AsyncMock())
    return bot


@pytest.mark.asyncio
async def test_process_upload_prepares_audio_before_transcribing(tmp_path, monkeypatch):
    source = tmp_path / "upload.mp4"
    source.write_bytes(b"video")
    audio = tmp_path / "prepared.mp3"
    audio.write_bytes(b"audio")
    calls = []
    bot = _mock_bot(message_id=42)

    async def fake_prepare(path: str, output_dir: str) -> str:
        calls.append(("prepare", path, output_dir))
        return str(audio)

    async def fake_transcribe(path: str) -> str:
        calls.append(("transcribe", path))
        return "готовый текст"

    send_transcript = AsyncMock()
    monkeypatch.setattr(webapp_main, "Bot", MagicMock(return_value=bot))
    monkeypatch.setattr(
        webapp_main,
        "prepare_audio_for_transcription",
        fake_prepare,
    )
    monkeypatch.setattr(webapp_main, "transcribe", fake_transcribe)
    monkeypatch.setattr(webapp_main, "send_transcript_to_chat", send_transcript)

    await webapp_main._process_upload(str(source), user_id=111)

    assert calls == [
        ("prepare", str(source), webapp_main.settings.TEMP_DIR),
        ("transcribe", str(audio)),
    ]
    send_transcript.assert_awaited_once_with(bot, 111, "готовый текст")
    bot.send_message.assert_awaited_once_with(111, "Файл получен. Готовлю аудио…")
    bot.edit_message_text.assert_awaited_once_with(
        "Аудио готово. Транскрибирую…",
        chat_id=111,
        message_id=42,
    )
    bot.delete_message.assert_awaited_once_with(chat_id=111, message_id=42)
    bot.session.close.assert_awaited_once()
    assert not source.exists()
    assert not audio.exists()


@pytest.mark.asyncio
async def test_process_upload_reports_failure_and_cleans_source(tmp_path, monkeypatch):
    source = tmp_path / "broken.mov"
    source.write_bytes(b"broken")
    bot = _mock_bot(message_id=7)

    async def fake_prepare(_path: str, _output_dir: str) -> str:
        raise RuntimeError("bad media")

    transcribe = AsyncMock()
    send_transcript = AsyncMock()
    monkeypatch.setattr(webapp_main, "Bot", MagicMock(return_value=bot))
    monkeypatch.setattr(
        webapp_main,
        "prepare_audio_for_transcription",
        fake_prepare,
    )
    monkeypatch.setattr(webapp_main, "transcribe", transcribe)
    monkeypatch.setattr(webapp_main, "send_transcript_to_chat", send_transcript)

    await webapp_main._process_upload(str(source), user_id=111)

    transcribe.assert_not_awaited()
    send_transcript.assert_not_awaited()
    bot.edit_message_text.assert_awaited_once_with(
        webapp_main.UPLOAD_ERROR_TEXT,
        chat_id=111,
        message_id=7,
    )
    bot.session.close.assert_awaited_once()
    assert not source.exists()
