"""Установка библиотек для Crosspost. Запускается из Install.bat."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    print("Crosspost: установка библиотек\n")
    if sys.version_info < (3, 11):
        print(f"Нужен Python 3.11 или новее, а установлен {sys.version.split()[0]}.")
        print("Скачайте свежий Python с python.org (при установке отметьте «Add Python to PATH»).")
        return 1
    print(f"Python {sys.version.split()[0]} — подходит.\n")

    pip = [sys.executable, "-m", "pip"]
    subprocess.call(pip + ["install", "--upgrade", "pip"])
    code = subprocess.call(pip + ["install", "-r", str(ROOT / "requirements-app.txt")])
    print()
    if code != 0:
        print("Не все библиотеки установились — сообщение об ошибке выше.")
        print("Пришлите его мне, разберёмся.")
        return code

    # FakeTLS-прокси (MTProxy с секретом ee…) — отдельно: если не поставится, остальное работает
    if subprocess.call(pip + ["install", "TelethonFakeTLS"]) != 0:
        print("Библиотека TelethonFakeTLS не установилась — MTProxy с секретом ee… работать не будет (остальное — будет).")
    missing = []
    for mod, name in [("pandas", "pandas"), ("numpy", "numpy"), ("requests", "requests"), ("webview", "pywebview"), ("telethon", "telethon"), ("python_socks", "python-socks"), ("playwright", "playwright"), ("pystray", "pystray"), ("psutil", "psutil")]:
        try:
            __import__(mod)
        except Exception as e:  # noqa: BLE001
            missing.append(f"{name} ({e})")
    if missing:
        print("Установка прошла, но не загружаются: " + ", ".join(missing))
        if all(m.startswith("pywebview") for m in missing):
            print("Без pywebview приложение откроется во вкладке браузера — это нормально, работать будет.")
        return 0
    print("Готово! Запускайте Crosspost.bat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
