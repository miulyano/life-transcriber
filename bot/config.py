from functools import cached_property
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    BOT_TOKEN: str
    OPENAI_API_KEY: str
    ALLOWED_USER_IDS: str  # comma-separated list, parsed via property
    LONG_TEXT_THRESHOLD: int = 2000
    MIN_SUMMARY_LEN: int = 500
    WHISPER_MODEL: str = "whisper-1"
    GPT_MODEL: str = "gpt-4o"
    TEMP_DIR: str = "/tmp/transcriber"
    COBALT_API_URL: str = "http://cobalt:9000"
    WEBAPP_URL: Optional[str] = None  # https://transcriber.<domain> — enables bot menu button

    @cached_property
    def allowed_user_ids(self) -> list[int]:
        return [int(uid.strip()) for uid in self.ALLOWED_USER_IDS.split(",") if uid.strip()]


settings = Settings()
