from functools import cached_property
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    BOT_TOKEN: str
    OPENAI_API_KEY: str
    ASSEMBLYAI_API_KEY: str
    ALLOWED_USER_IDS: str  # comma-separated list, parsed via property
    LONG_TEXT_THRESHOLD: int = 2000
    MIN_SUMMARY_LEN: int = 500
    ASSEMBLYAI_SPEECH_MODEL: str = "universal"  # universal | nano | slam-1
    WORD_BOOST_FILE: str = "bot/data/word_boost.txt"
    CUSTOM_SPELLING_FILE: str = "bot/data/custom_spelling.json"
    WORD_BOOST_LEVEL: str = "high"  # low | default | high
    # Autodetect by default. Set e.g. FORCE_LANGUAGE_CODE=ru to skip detection
    # — useful if your audio is always one known language and you want max
    # accuracy on short clips (autodetect is unreliable below ~30 sec).
    FORCE_LANGUAGE_CODE: Optional[str] = None
    GPT_MODEL: str = "gpt-4o"
    TEMP_DIR: str = "/tmp/transcriber"
    COBALT_API_URL: str = "http://cobalt:9000"
    INSTAGRAM_COOKIES_PATH: Optional[str] = None
    YTDLP_PROXY: Optional[str] = None
    YANDEX_MUSIC_PROXY: Optional[str] = None
    WEBAPP_URL: Optional[str] = None  # https://transcriber.<domain> — enables bot menu button

    @cached_property
    def allowed_user_ids(self) -> list[int]:
        return [int(uid.strip()) for uid in self.ALLOWED_USER_IDS.split(",") if uid.strip()]


settings = Settings()
