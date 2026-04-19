import os
import uuid

from bot.services.ffmpeg_runner import run_ffmpeg


async def prepare_audio_for_transcription(input_path: str, output_dir: str) -> str:
    """Convert any supported media file to compact audio-only mp3."""
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{uuid.uuid4().hex}.mp3")

    try:
        await run_ffmpeg(
            "-i",
            input_path,
            "-vn",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-acodec",
            "mp3",
            out_path,
        )
    except RuntimeError:
        if os.path.exists(out_path):
            os.unlink(out_path)
        raise
    return out_path
