"""Журнал ошибок фоновых дел (app.log в папке данных): то, что раньше молча проглатывалось.

Один и тот же текст чаще раза в минуту не пишется — иначе сбой, повторяющийся каждые 15 секунд, раздул бы файл.
Файл не больше 512 КБ (старый уходит в app.log.1).
"""
import threading
import time
import traceback
from datetime import datetime

from app.paths import data_dir

_lock = threading.Lock()
_last: dict = {}
MAX_BYTES = 512 * 1024
SAME_S = 60


def log(msg: str, exc: bool = True):
    """Записать сообщение; exc=True — вместе с трассировкой текущего исключения."""
    try:
        now = time.time()
        with _lock:
            if now - _last.get(msg, 0) < SAME_S:
                return
            if len(_last) > 200:
                _last.clear()
            _last[msg] = now
            p = data_dir() / "app.log"
            if p.exists() and p.stat().st_size > MAX_BYTES:
                p.replace(p.with_suffix(".log.1"))
            from app.jobs import mask_secrets
            with open(p, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {mask_secrets(msg)}\n")
                if exc:
                    tb = traceback.format_exc()
                    if tb and not tb.startswith("NoneType"):
                        f.write(mask_secrets(tb))
    except Exception:  # noqa: BLE001 — журнал не должен ронять само приложение
        pass
