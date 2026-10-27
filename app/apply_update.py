"""Замена файлов программы на новую версию. Запускается ОТДЕЛЬНЫМ процессом (его копию кладёт selfupdate.py
в папку данных), потому что заменяет в том числе саму папку app. Только стандартная библиотека.

Порядок: ждём, пока окно закроется → старые файлы переносим в резервную папку → ставим новые →
проверяем, что новая версия запускается (import app.main) → запускаем программу. Любая ошибка — откат на старые файлы.
Результат пишется в update-result.json в папке данных; окно покажет его при следующем запуске.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

KEEP = {"config.toml", "tokens.json", "Файлы", "Files", "data", "lpub", ".git", ".update-backup", "venv", ".venv", "__pycache__"}


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259   # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def log(path: Path, msg: str):
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
    except OSError:
        pass


def rmtree(p: Path):
    for _ in range(5):   # Windows бывает ещё секунду держит файлы
        try:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
            return
        except FileNotFoundError:
            return
        except OSError:
            time.sleep(1)
    shutil.rmtree(p, ignore_errors=True)


def popen_detached(cmd, cwd):
    kw = {"cwd": str(cwd), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(cmd, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)      # папка программы
    ap.add_argument("--new", required=True)       # распакованная новая версия
    ap.add_argument("--data", required=True)      # папка данных
    ap.add_argument("--pid", type=int, default=0)  # окно, которое надо дождаться
    ap.add_argument("--version", default="")
    ap.add_argument("--python", default=sys.executable)    # python.exe — для pip и проверки
    ap.add_argument("--pythonw", default=sys.executable)   # pythonw.exe — для запуска окна
    ap.add_argument("--no-restart", action="store_true")
    a = ap.parse_args()
    root, new, data = Path(a.root), Path(a.new), Path(a.data)
    upd = data / "updates"
    lg = upd / "update.log"
    result = data / "update-result.json"
    backup = upd / "backup"
    done: list[str] = []      # что уже заменили
    moved: list[str] = []     # что убрали в резерв

    def finish(ok: bool, error: str = ""):
        result.write_text(json.dumps({"ok": ok, "version": a.version, "error": error, "at": time.time()}, ensure_ascii=False), encoding="utf-8")
        log(lg, "готово" if ok else f"ОШИБКА: {error}")
        if not a.no_restart:
            try:
                popen_detached([a.pythonw, "-m", "app"], root)
            except OSError as e:
                log(lg, f"не удалось запустить программу: {e}")
        return 0 if ok else 1

    def rollback():
        for n in done:
            rmtree(root / n)
        for n in moved:
            src = backup / n
            if src.exists():
                shutil.move(str(src), str(root / n))
        log(lg, "откат выполнен")

    try:
        log(lg, f"обновление до {a.version}: старт")
        t0 = time.time()
        while pid_alive(a.pid) and time.time() - t0 < 90:
            time.sleep(0.5)
        if pid_alive(a.pid):
            return finish(False, "окно Crosspost не закрылось — файлы не тронуты")
        time.sleep(1)
        rmtree(backup)
        backup.mkdir(parents=True, exist_ok=True)
        old_req = (root / "requirements-app.txt").read_text(encoding="utf-8") if (root / "requirements-app.txt").exists() else ""
        names = [p.name for p in new.iterdir() if p.name not in KEEP]
        for n in names:
            if (root / n).exists():
                shutil.move(str(root / n), str(backup / n))
            moved.append(n)
            shutil.move(str(new / n), str(root / n))
            done.append(n)
        log(lg, f"заменено: {', '.join(names)}")
        new_req = (root / "requirements-app.txt").read_text(encoding="utf-8") if (root / "requirements-app.txt").exists() else ""
        if new_req != old_req:
            log(lg, "список библиотек изменился — ставлю")
            r = subprocess.run([a.python, "-m", "pip", "install", "-r", str(root / "requirements-app.txt")], cwd=root, capture_output=True, text=True, timeout=900)
            log(lg, (r.stdout + r.stderr)[-1500:])
            if r.returncode != 0:
                raise RuntimeError("не установились новые библиотеки (подробности — в updates\\update.log)")
        r = subprocess.run([a.python, "-c", "import app.main"], cwd=root, capture_output=True, text=True, timeout=180)
        if r.returncode != 0:
            log(lg, r.stderr[-1500:])
            raise RuntimeError("новая версия не запускается: " + (r.stderr.strip().splitlines() or ["ошибка"])[-1][:200])
        rmtree(upd / "new")
        rmtree(upd / "dl")
        (upd / "ready.json").unlink(missing_ok=True)
        return finish(True)
    except Exception as e:  # noqa: BLE001
        log(lg, f"сбой: {e!r}")
        try:
            rollback()
        except Exception as e2:  # noqa: BLE001
            log(lg, f"откат не удался: {e2!r}")
            return finish(False, f"{e}. Откат не удался — старая версия лежит в {backup}")
        return finish(False, str(e))


if __name__ == "__main__":
    sys.exit(main())
