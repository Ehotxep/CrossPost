"""Обращение к теггеру в отдельном процессе (app/tagger/serve.py) — окно приложения не держит модель в памяти.

Процесс запускается при первой картинке (модель грузится на видеокарту ~10–60 с) и закрывается через
IDLE секунд без работы (или кнопкой «Выгрузить теггер»). Пока пишется пост и добавляются картинки, он живёт — повторной загрузки нет.
"""
import atexit
import json
import os
import subprocess
import threading
import time

IDLE = 120   # 2 минуты без работы — процесс с моделью закрывается и отдаёт память

_lock = threading.Lock()
_proc = None
_last = 0.0
_device = None
starting = False   # модель как раз грузится в видеокарту — для панели процессов


def alive() -> bool:
    return _proc is not None and _proc.poll() is None


def device():
    return _device if alive() else None


def _start():
    global starting
    starting = True
    try:
        _start_inner()
    finally:
        starting = False


def _start_inner():
    global _proc, _device
    from app import subproc
    from app.paths import data_dir
    from app import __file__ as app_init
    root = os.path.dirname(os.path.dirname(os.path.abspath(app_init)))
    env = dict(os.environ, CROSSPOST_DATA=str(data_dir()), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    _proc = subprocess.Popen([subproc.python_exe(), "-m", "app.tagger.serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, cwd=root, env=env, creationflags=subproc.NOWIN,
                             text=True, encoding="utf-8", errors="replace", bufsize=1)
    first = _read()
    if "ready" not in first:
        stop()
        raise RuntimeError(first.get("err") or "теггер не запустился")
    _device = first["ready"]
    threading.Thread(target=_watch, daemon=True).start()


def _read() -> dict:
    while True:
        line = _proc.stdout.readline()
        if not line:
            return {"err": "процесс теггера закрылся"}
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue


def _watch():
    me = _proc
    while True:
        time.sleep(15)
        with _lock:
            if _proc is not me or not alive():
                return
            if time.time() - _last > IDLE:
                stop()
                return


def stop():
    global _proc
    p, _proc = _proc, None
    if p and p.poll() is None:
        try:
            p.stdin.close()
            p.wait(timeout=10)
        except Exception:
            p.kill()


def predict(path, min_prob: float = 0.1) -> list:
    global _last
    with _lock:
        if not alive():
            _start()
        _last = time.time()
        _proc.stdin.write(json.dumps({"path": str(path), "min": min_prob}) + "\n")
        _proc.stdin.flush()
        r = _read()
        _last = time.time()
    if "err" in r:
        raise RuntimeError(r["err"])
    return [tuple(x) for x in r["ok"]]


atexit.register(stop)   # окно закрыли — теггер закрывается вместе с ним
