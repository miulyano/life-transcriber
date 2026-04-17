"""Tests for Telegram WebApp initData HMAC validation."""
import hashlib
import hmac
import json
import time
from typing import Optional
from urllib.parse import urlencode

import pytest

from webapp.auth import validate_init_data


BOT_TOKEN = "test_bot_token"


def _make_init_data(user_id: int = 123, auth_date: Optional[int] = None, tamper: bool = False) -> str:
    """Build a valid (or tampered) initData string."""
    if auth_date is None:
        auth_date = int(time.time())

    user = json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":"))
    fields = {"auth_date": str(auth_date), "user": user}

    data_check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    correct_hash = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()

    if tamper:
        # flip last char
        correct_hash = correct_hash[:-1] + ("0" if correct_hash[-1] != "0" else "1")

    fields["hash"] = correct_hash
    return urlencode(fields)


def test_valid_init_data():
    init_data = _make_init_data(user_id=555)
    result = validate_init_data(init_data, BOT_TOKEN)
    assert result is not None
    assert result["user_id"] == 555
    assert isinstance(result["auth_date"], int)


def test_tampered_hash_returns_none():
    init_data = _make_init_data(tamper=True)
    assert validate_init_data(init_data, BOT_TOKEN) is None


def test_missing_hash_returns_none():
    # initData without hash field
    init_data = "auth_date=1234567890&user=%7B%22id%22%3A1%7D"
    assert validate_init_data(init_data, BOT_TOKEN) is None


def test_wrong_bot_token_returns_none():
    init_data = _make_init_data(user_id=1)
    assert validate_init_data(init_data, "wrong_token") is None


def test_returns_correct_user_id_and_auth_date():
    now = int(time.time())
    init_data = _make_init_data(user_id=999, auth_date=now)
    result = validate_init_data(init_data, BOT_TOKEN)
    assert result["user_id"] == 999
    assert result["auth_date"] == now
