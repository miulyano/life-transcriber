from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    BOT_TOKEN: str
    OPENAI_API_KEY: str
    ALLOWED_USER_IDS: list[int]
    LONG_TEXT_THRESHOLD: int = 2000
    WHISPER_MODEL: str = "whisper-1"
    GPT_MODEL: str = "gpt-4o"
    TEMP_DIR: str = "/tmp/transcriber"

    @field_validator("ALLOWED_USER_IDS", mode="before")
    @classmethod
    def parse_user_ids(cls, v: str | list) -> list[int]:
        if isinstance(v, str):
            return [int(uid.strip()) for uid in v.split(",") if uid.strip()]
        return v


settings = Settings()
