"""Мелкие общие помощники для текстов."""


def plural(n, one: str, few: str, many: str) -> str:
    """Форма слова по числу: 1 пост, 2 поста, 5 постов (11–14 — «постов»)."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many
