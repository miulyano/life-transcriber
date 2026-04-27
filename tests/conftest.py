import os

# Set env vars BEFORE any test imports bot.config (Settings() runs at import time).
# os.environ takes precedence over .env in pydantic-settings, so local secrets
# won't leak into tests.
os.environ["BOT_TOKEN"] = "test_token"
os.environ["OPENAI_API_KEY"] = "test_key"
os.environ["ASSEMBLYAI_API_KEY"] = "test_aai_key"
os.environ["ALLOWED_USER_IDS"] = "111,222"
