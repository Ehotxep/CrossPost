"""Crosspost — настольное приложение: публикация, статистика и тренды."""
__version__ = "0.2.9"

from app import netfix as _netfix  # noqa: E402 — запасной поиск адресов, если DNS не нашёл сайт

_netfix.install()
