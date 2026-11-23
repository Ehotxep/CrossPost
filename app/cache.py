"""Сохранённые результаты тяжёлых расчётов: экран открывается сразу с прошлыми данными, пересчёт — в фоне.

Как работает get(name, compute, key):
  • есть сохранённый результат — отдаём его сразу; если данные с тех пор поменялись (другой key)
    или он старше max_age — запускаем пересчёт в фоне, интерфейс через пару секунд спросит снова;
  • ничего нет — считаем сразу (один раз).
Результаты лежат в папке данных (cache/*.json): их готовит и окно приложения, и фоновая отправка,
поэтому к моменту открытия Crosspost всё обычно уже посчитано.
"""
from core.fsutil import file_lock

import json
import re
import threading
import time
import traceback

from app.paths import sub

_lock = threading.Lock()
_running: set = set()
_mem: dict = {}


def _path(name: str):
    return sub("cache") / (re.sub(r"[^\w.-]+", "_", name) + ".json")


def read(name: str) -> dict | None:
    hit = _mem.get(name)
    p = _path(name)
    try:
        mt = p.stat().st_mtime
    except OSError:
        return hit[1] if hit else None
    if hit and hit[0] == mt:
        return hit[1]
    try:
        obj = clean(json.loads(p.read_text(encoding="utf-8")))   # старые файлы могли сохраниться с NaN
    except Exception:
        return None
    _mem[name] = (mt, obj)
    return obj


def clean(v):
    """NaN/Infinity (пустые значения из pandas) → None: иначе браузер не разберёт JSON."""
    if isinstance(v, float):
        return None if v != v or v in (float("inf"), float("-inf")) else v
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    try:
        import numpy as np
        if isinstance(v, np.generic):
            return clean(v.item())
    except Exception:
        pass
    return v


def write(name: str, data, key) -> dict:
    obj = {"at": time.time(), "key": _norm(key), "data": clean(data)}
    p = _path(name)
    tmp = p.with_name(p.name + f".{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(p)
    _mem.pop(name, None)
    return obj


def _norm(key):
    return json.loads(json.dumps(key, default=str))


def refresh(name: str, compute, key, max_age=0) -> bool:
    """Пересчитать в фоновом потоке (если такой пересчёт уже не идёт)."""
    with _lock:
        if name in _running:
            return False
        _running.add(name)

    def run():
        try:
            warm(name, compute, key, max_age=max_age)
        except Exception:
            traceback.print_exc()
        finally:
            with _lock:
                _running.discard(name)
    threading.Thread(target=run, daemon=True, name=f"cache:{name}").start()
    return True


def busy(name: str) -> bool:
    with _lock:
        return name in _running


def get(name: str, compute, key, max_age: float = 6 * 3600, *, accept_cached=None) -> dict:
    """{"data", "at", "stale", "refreshing"} — сразу, без ожидания пересчёта (кроме самого первого раза)."""
    obj = read(name)
    if obj is None or (accept_cached is not None and not accept_cached(obj["data"])):
        with file_lock(_path(name)):
            obj = read(name)
            if obj is None or obj.get("key") != _norm(key) or (accept_cached and not accept_cached(obj["data"])):
                obj = write(name, compute(), key)
        return {"data": obj["data"], "at": obj["at"], "stale": False, "refreshing": False}
    stale = obj.get("key") != _norm(key) or time.time() - obj.get("at", 0) > max_age
    if stale:
        refresh(name, compute, key, max_age)
    return {"data": obj["data"], "at": obj["at"], "stale": stale, "refreshing": busy(name)}


def warm(name: str, compute, key, max_age: float = 6 * 3600) -> bool:
    """Для фоновой отправки: посчитать заранее, если сохранённое устарело. Возвращает True, если пересчитал."""
    with file_lock(_path(name)):
        obj = read(name)
        if obj is not None and obj.get("key") == _norm(key) and time.time() - obj.get("at", 0) <= max_age:
            return False
        write(name, compute(), key)
        return True
