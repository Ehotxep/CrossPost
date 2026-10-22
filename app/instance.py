"""Один экземпляр окна Crosspost.

Два окна сразу — это два входа в Telegram (Telegram может аннулировать ключ входа), две записи в одну базу
и два браузера для ВК. Поэтому первое окно держит замок app.lock в папке данных и записывает свой адрес
в app.instance. Второй запуск видит замок, просит первое окно развернуться и закрывается сам.
Замок снимает система, когда процесс завершается (даже аварийно) — «залипнуть» он не может.
"""
import json
import sys

from app.paths import data_dir

_lock = None


def acquire() -> bool:
    """True — мы первые (замок наш до конца процесса)."""
    global _lock
    from app.telegram.backend import SessionLock
    lk = SessionLock(data_dir() / "app.lock")
    if not lk.try_acquire():
        return False
    _lock = lk
    return True


def publish(url: str, token: str):
    p = data_dir() / "app.instance"
    try:   # файл только в вашей папке данных; токен действует, пока это окно открыто
        p.write_text(json.dumps({"url": url, "token": token}), encoding="utf-8")
    except OSError:
        pass


def wake_existing() -> bool:
    """Попросить уже открытое окно выйти на передний план."""
    import urllib.request
    try:
        d = json.loads((data_dir() / "app.instance").read_text(encoding="utf-8"))
        req = urllib.request.Request(d["url"].rstrip("/") + "/api/focus", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json", "X-Token": d["token"]})
        with urllib.request.urlopen(req, timeout=5) as r:
            res = json.loads(r.read().decode("utf-8")).get("result") or {}
        if not res.get("window"):   # открыт в браузере, а не в своём окне — откроем вкладку
            import webbrowser
            webbrowser.open(d["url"])
        return True
    except Exception:  # noqa: BLE001
        return False


def tell(msg: str):
    """Сообщение, когда окна ещё нет: в Windows — системное окошко, иначе — в консоль."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, "Crosspost", 0x40)
            return
        except Exception:  # noqa: BLE001
            pass
    print(msg)
