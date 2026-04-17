import hashlib
import hmac
import json
from typing import Optional
from urllib.parse import parse_qsl


def validate_init_data(init_data: str, bot_token: str) -> Optional[dict]:
    """Validate Telegram WebApp initData via HMAC-SHA256.

    Returns dict with user_id and auth_date if valid, None otherwise.
    See: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    parsed = dict(parse_qsl(init_data, keep_blank_values=True))
    received = parsed.pop("hash", None)
    if not received:
        return None

    data_check = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed, received):
        return None

    user = json.loads(parsed.get("user", "{}"))
    return {
        "user_id": user.get("id"),
        "auth_date": int(parsed.get("auth_date", "0")),
    }
