import asyncio
import os
import uuid


async def prepare_audio_for_transcription(input_path: str, output_dir: str) -> str:
    """Convert any supported media file to compact audio-only mp3."""
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{uuid.uuid4().hex}.mp3")

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-i", input_path,
        "-vn",
        "-ar", "16000",
        "-ac", "1",
        "-acodec", "mp3",
        out_path,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()

    if proc.returncode != 0:
        if os.path.exists(out_path):
            os.unlink(out_path)
        raise RuntimeError(f"ffmpeg failed with code {proc.returncode}")
    return out_path
