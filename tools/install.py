"""Установка библиотек для Crosspost. Запускается из Install.bat."""
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def environment_python(root: Path) -> Path:
    return root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def prepare_environment(root: Path) -> Path:
    """Создать или проверить окружение, не используя пакеты системного Python."""
    env = root / ".venv"
    python = environment_python(root)
    if not python.exists():
        print(f"Создаю окружение: {env}", flush=True)
        venv.EnvBuilder(with_pip=True, system_site_packages=False).create(env)
    subprocess.run([str(python), "-c",
                    "import sys; from pathlib import Path; "
                    "assert sys.version_info >= (3, 11), 'Python 3.11+ required'; "
                    "assert sys.prefix != sys.base_prefix, 'Not a virtual environment'; "
                    "assert Path(sys.prefix).resolve() == Path(sys.argv[1]).resolve(), 'Wrong environment'",
                    str(env)], check=True, capture_output=True, text=True)
    # Запрещаем доступ к системным пакетам и для ранее созданного окружения.
    cfg = env / "pyvenv.cfg"
    lines = cfg.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if not line.lower().startswith("include-system-site-packages")]
    cfg.write_text("\n".join(lines + ["include-system-site-packages = false"]) + "\n", encoding="utf-8")
    return python


def main() -> int:
    print("Crosspost: установка библиотек\n")
    if sys.version_info < (3, 11):
        print(f"Нужен Python 3.11 или новее, а установлен {sys.version.split()[0]}.")
        print("Скачайте свежий Python с python.org (при установке отметьте «Add Python to PATH»).")
        return 1
    print(f"Python {sys.version.split()[0]} — подходит.\n")

    try:
        python = prepare_environment(ROOT)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"Не удалось подготовить .venv: {e}")
        print("Закройте Crosspost и фоновую отправку. Если ошибка повторяется, удалите только папку .venv рядом с программой и снова запустите Install.bat.")
        return 1
    if Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
        return subprocess.call([str(python), str(Path(__file__).resolve())], cwd=ROOT)

    print(f"Зависимости устанавливаются только в {sys.prefix}\n", flush=True)
    ready = ROOT / ".venv" / ".crosspost-ready"
    ready.unlink(missing_ok=True)
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
    for mod, name in [("pandas", "pandas"), ("numpy", "numpy"), ("requests", "requests"), ("PIL", "Pillow"), ("webview", "pywebview"), ("telethon", "telethon"), ("python_socks", "python-socks"), ("playwright", "playwright"), ("pystray", "pystray"), ("psutil", "psutil")]:
        try:
            __import__(mod)
        except Exception as e:  # noqa: BLE001
            missing.append(f"{name} ({e})")
    if missing:
        print("Установка прошла, но не загружаются: " + ", ".join(missing))
        if all(m.startswith("pywebview") for m in missing):
            print("Без pywebview приложение откроется во вкладке браузера — это нормально, работать будет.")
        else:
            return 1
    if sys.platform == "win32":
        code = subprocess.call([sys.executable, "-c",
                                "from app import worker; "
                                "worker.set_autostart(True) if worker.autostart_path().exists() else None"], cwd=ROOT)
        if code:
            print("Не удалось обновить путь автозапуска. Выключите и включите автозапуск в настройках Crosspost.")
    ready.write_text("Dependencies installed successfully.\n", encoding="utf-8")
    print("Готово! Запускайте Crosspost.bat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
