# Telegram rejects text messages over 4096 chars with
# "Bad Request: message is too long". We leave headroom for the
# "📝 Краткий конспект:\n\n" prefix and for HTML tags that
# ``markdown_to_telegram_html`` may inject (each `<b>...</b>` adds ≥7 chars,
# and long summaries can accumulate dozens of them).
TELEGRAM_TEXT_LIMIT = 3800
