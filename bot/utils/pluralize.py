from __future__ import annotations


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    n_abs = abs(int(n))
    mod100 = n_abs % 100
    mod10 = n_abs % 10
    if 11 <= mod100 <= 14:
        return many
    if mod10 == 1:
        return one
    if 2 <= mod10 <= 4:
        return few
    return many


def format_hm(seconds: float) -> str:
    """Round seconds to the nearest minute and render as RU 'X часов Y минут'.

    Edge cases:
    - 0 seconds → "0 минут"
    - whole hours → "N часов"
    - less than a hour → "M минут"
    """
    total_minutes = int(max(0.0, float(seconds)) / 60.0 + 0.5)
    hours, minutes = divmod(total_minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} {plural_ru(hours, 'час', 'часа', 'часов')}")
    if minutes or not hours:
        parts.append(f"{minutes} {plural_ru(minutes, 'минута', 'минуты', 'минут')}")
    return " ".join(parts)
