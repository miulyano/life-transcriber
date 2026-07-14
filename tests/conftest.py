import os
import tempfile

import pytest

# Set env vars BEFORE any test imports bot.config (Settings() runs at import time).
# os.environ takes precedence over .env in pydantic-settings, so local secrets
# won't leak into tests.
os.environ["BOT_TOKEN"] = "test_token"
os.environ["OPENAI_API_KEY"] = "test_key"
os.environ["ASSEMBLYAI_API_KEY"] = "test_aai_key"
os.environ["ALLOWED_USER_IDS"] = "111,222"

# Point all persistent stores at a throwaway dir: the webapp lifespan touches
# the DB on startup, so TestClient(app) context managers would otherwise write
# into the repo-local data/ directory. Mutating the singleton (not env vars)
# keeps Settings() defaults intact for test_config.py.
_test_data_dir = tempfile.mkdtemp(prefix="transcriber_test_data_")

from bot.config import settings  # noqa: E402

settings.TRANSCRIPTS_DB_FILE = os.path.join(_test_data_dir, "transcripts.db")
settings.TRANSCRIPTS_DIR = os.path.join(_test_data_dir, "transcripts")
settings.USAGE_FILE = os.path.join(_test_data_dir, "usage.json")


@pytest.fixture(autouse=True)
def _reset_task_registry():
    """Fresh semaphore and task registry per test — the lazy semaphore must
    not leak between event loops (pytest-asyncio creates a loop per test)."""
    from bot.services import task_registry

    task_registry._semaphore = None
    task_registry._running.clear()
    yield
    task_registry._semaphore = None
    task_registry._running.clear()
