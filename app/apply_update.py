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
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        h = kernel.OpenProcess(0x1000, False, pid)
        if not h:
            # Only ERROR_INVALID_PARAMETER proves that the PID is gone.
            if ctypes.get_last_error() == 87:
                return False
            raise OSError(ctypes.get_last_error(), "Не удалось проверить завершение старого процесса")
        try:
            code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(h, ctypes.byref(code)):
                raise OSError(ctypes.get_last_error(), "Не удалось прочитать состояние старого процесса")
            return code.value == 259
        finally:
            kernel.CloseHandle(h)
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


def launch_environment():
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    return env


def popen_detached(cmd, cwd, logfile=None):
    kw = {"env": launch_environment(), "cwd": str(cwd), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True}
    if sys.platform == "win32":
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000
    else:
        kw["start_new_session"] = True
    if logfile is not None:
        with open(logfile, "ab") as stream:
            kw.update(stdout=stream, stderr=stream)
            return subprocess.Popen(cmd, **kw)
    return subprocess.Popen(cmd, **kw)


def wait_for_restart(process, data: Path, version: str, timeout=60):
    """Verify the new process owns a responsive local server, not a stale instance."""
    import urllib.request
    from urllib.parse import urlparse
    deadline = time.monotonic() + timeout
    client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False, "Новый процесс завершился до запуска интерфейса; подробности — updates/update.log"
        try:
            info = json.loads((data / "app.instance").read_text(encoding="utf-8"))
            url = info["url"]
            parsed = urlparse(url)
            if (info.get("pid") == process.pid and info.get("version") == version
                    and parsed.scheme == "http" and parsed.hostname == "127.0.0.1"):
                req = urllib.request.Request(url.rstrip("/") + "/api/info", data=b"{}",
                        headers={"Content-Type": "application/json", "X-Token": info["token"]})
                with client.open(req, timeout=2) as response:
                    body = json.loads(response.read())
                if body.get("ok"):
                    return True, ""
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(.25)
    return False, "Автозапуск новой версии не подтверждён; подробности — updates/update.log"


def dependency_signature(root: Path):
    return tuple((root / name).read_text(encoding='utf-8') if (root / name).exists() else ''
                 for name in ('requirements-app.txt', 'requirements-constraints.txt'))


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
    root, new, data = Path(a.root).resolve(), Path(a.new).resolve(), Path(a.data).resolve()
    os.environ["CROSSPOST_DATA"] = str(data)
    upd = data / "updates"
    lg = upd / "update.log"
    result = data / "update-result.json"
    backup = upd / "backup"
    done: list[str] = []      # что уже заменили
    moved: list[str] = []     # что убрали в резерв

    def finish(ok: bool, error: str = ""):
        # Publish installation status before restart so the new window can read it.
        result.write_text(json.dumps({"ok": ok, "version": a.version, "error": error, "at": time.time()}, ensure_ascii=False), encoding="utf-8")
        log(lg, "файлы установлены" if ok else f"ОШИБКА: {error}")
        if not a.no_restart:
            try:
                child = popen_detached([a.pythonw, "-m", "app"], root, logfile=lg)
                import re
                init = (root / "app" / "__init__.py").read_text(encoding="utf-8")
                match = re.search(r'__version__\s*=\s*["\']([^"\']+)', init)
                restart_version = match.group(1) if match else a.version
                started, reason = wait_for_restart(child, data, restart_version)
                if not started:
                    log(lg, reason)
                    result.write_text(json.dumps({"ok": False, "version": a.version, "error": reason, "at": time.time()}, ensure_ascii=False), encoding="utf-8")
                    return 1
                log(lg, "новый процесс и сервер готовы")
            except OSError as e:
                log(lg, f"не удалось запустить программу: {e}")
                result.write_text(json.dumps({"ok": False, "version": a.version, "error": f"Не удалось перезапустить: {e}", "at": time.time()}, ensure_ascii=False), encoding="utf-8")
                return 1
        return 0 if ok else 1

    def rollback():
        for n in done:
            rmtree(root / n)
        for n in moved:
            src = backup / n
            if src.exists():
                shutil.move(str(src), str(root / n))
        log(lg, "откат выполнен")

    parent_stopped = False
    try:
        log(lg, f"обновление до {a.version}: старт")
        t0 = time.time()
        while pid_alive(a.pid) and time.time() - t0 < 90:
            time.sleep(0.5)
        if pid_alive(a.pid):
            a.no_restart = True
            return finish(False, "окно Crosspost не закрылось — файлы не тронуты")
        parent_stopped = True
        time.sleep(1)
        rmtree(backup)
        backup.mkdir(parents=True, exist_ok=True)
        old_req = dependency_signature(root)
        names = [p.name for p in new.iterdir() if p.name not in KEEP]
        for n in names:
            if (root / n).exists():
                shutil.move(str(root / n), str(backup / n))
            moved.append(n)
            shutil.move(str(new / n), str(root / n))
            done.append(n)
        log(lg, f"заменено: {', '.join(names)}")
        new_req = dependency_signature(root)
        if new_req != old_req:
            log(lg, "список библиотек изменился — ставлю")
            r = subprocess.run([a.python, "-m", "pip", "install", "-r", str(root / "requirements-app.txt")], cwd=root, env=launch_environment(), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
            log(lg, (r.stdout + r.stderr)[-1500:])
            if r.returncode != 0:
                raise RuntimeError("не установились новые библиотеки (подробности — в updates\\update.log)")
        r = subprocess.run([a.python, "-c", "import app.main, app.api"], cwd=root, env=launch_environment(), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        if r.returncode != 0:
            log(lg, r.stderr[-1500:])
            raise RuntimeError("новая версия не запускается: " + (r.stderr.strip().splitlines() or ["ошибка"])[-1][:200])
        rmtree(upd / "new")
        rmtree(upd / "dl")
        (upd / "ready.json").unlink(missing_ok=True)
        return finish(True)
    except Exception as e:  # noqa: BLE001
        log(lg, f"сбой: {e!r}")
        if not parent_stopped:
            a.no_restart = True
            return finish(False, str(e))
        try:
            rollback()
        except Exception as e2:  # noqa: BLE001
            log(lg, f"откат не удался: {e2!r}")
            return finish(False, f"{e}. Откат не удался — старая версия лежит в {backup}")
        return finish(False, str(e))


if __name__ == "__main__":
    sys.exit(main())
