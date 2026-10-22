"""Где приложение хранит свои данные (выгрузки, кэш, настройки)."""
import os
import sys
from pathlib import Path

APP_NAME = "Crosspost"


def app_dir() -> Path:
    """Каталог программы (там, где лежат Crosspost.bat и папка app)."""
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    env = os.environ.get("CROSSPOST_DATA")
    if env:
        p = Path(env)
    elif sys.platform == "win32":
        p = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
    else:
        p = Path.home() / ".local" / "share" / "crosspost"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sub(*parts: str) -> Path:
    p = data_dir().joinpath(*parts)
    p.mkdir(parents=True, exist_ok=True)
    return p
