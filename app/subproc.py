"""Тяжёлые задачи — в отдельном процессе, чтобы окно приложения не держало гигабайты памяти.

Python почти не возвращает системе память после больших расчётов (разбор выгрузки e621 на миллионы постов,
модель теггера на видеокарте): процесс остаётся «толстым», пока его не закрыть. Поэтому такие задачи
запускаются дочерним процессом: он считает, пишет результат в файлы и закрывается — память освобождается вся.

Родитель:  run(report, "app.trends.jobs:update", "e621", ua=...)  — прогресс приходит через report, как обычно.
Дочерний:  python -m app.subproc <module:function> <json args>  — печатает строки JSON:
           {"p": 0.4, "m": "…"} — прогресс, {"ok": результат} или {"err": "текст"} — в конце.
"""
import importlib
import json
import os
import subprocess
import sys
import threading

NOWIN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def python_exe() -> str:
    """Для дочернего процесса нужен python.exe с консольным выводом (pythonw.exe не даёт stdout)."""
    exe = sys.executable
    if sys.platform == "win32" and exe.lower().endswith("pythonw.exe"):
        alt = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.exists(alt):
            return alt
    return exe


def run(report, target: str, *args, **kwargs):
    from app.paths import data_dir
    from app import __file__ as app_init
    root = os.path.dirname(os.path.dirname(os.path.abspath(app_init)))
    env = dict(os.environ, CROSSPOST_DATA=str(data_dir()), PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    p = subprocess.Popen([python_exe(), "-m", "app.subproc", target, json.dumps({"args": args, "kwargs": kwargs}, ensure_ascii=False)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=root, env=env, creationflags=NOWIN,
                         text=True, encoding="utf-8", errors="replace")
    err_tail: list = []

    def drain():   # stderr читаем отдельно, иначе при большом выводе процесс зависнет
        for line in p.stderr:
            err_tail.append(line.rstrip())
            del err_tail[:-20]
    threading.Thread(target=drain, daemon=True).start()
    result, error = None, None
    for line in p.stdout:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "ok" in msg:
            result = msg["ok"]
        elif "err" in msg:
            error = msg["err"]
        else:
            report(**{k: v for k, v in (("progress", msg.get("p")), ("message", msg.get("m"))) if v is not None})
    p.wait()
    if error:
        raise RuntimeError(error)
    if p.returncode != 0:
        tail = "\n".join(err_tail[-3:])
        raise RuntimeError(f"фоновая задача прервалась (код {p.returncode}){': ' + tail if tail else ''}")
    return result


def _child(target: str, payload: str):
    mod, fn = target.split(":")
    f = getattr(importlib.import_module(mod), fn)
    a = json.loads(payload)

    def report(progress=None, message=None, **_):
        print(json.dumps({"p": progress, "m": message}, ensure_ascii=False), flush=True)
    try:
        res = f(report, *a.get("args", []), **a.get("kwargs", {}))
        print(json.dumps({"ok": res}, ensure_ascii=False, default=str), flush=True)
    except Exception as e:  # noqa: BLE001 — текст ошибки показываем человеку
        import traceback
        traceback.print_exc()
        from app.jobs import mask_secrets
        print(json.dumps({"err": mask_secrets(str(e) or type(e).__name__)}, ensure_ascii=False), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    _child(sys.argv[1], sys.argv[2])
