"""Заранее готовим лёгкие JPEG, пока человек пишет текст, — чтобы публикация не ждала конвертации.

Лёгкая версия считается сразу после добавления файла по текущему рецепту (раздел «Лёгкая версия»)
и кладётся рядом с загрузкой: uploads/<id>.light-<рецепт>.jpg. При публикации берётся готовая,
если она влезает в лимиты площадки; иначе пережимается под площадку на месте.
"""
import hashlib
import json
import threading
from pathlib import Path

from app.paths import sub

_lock = threading.Lock()
_threads: dict = {}


def _key(recipe: dict) -> str:
    return hashlib.sha1(json.dumps(recipe, sort_keys=True).encode()).hexdigest()[:10]


def _paths(uid: str, recipe: dict):
    folder, name = sub("uploads"), f"{uid}.light-{_key(recipe)}"
    return folder / f"{name}.jpg", folder / f"{name}.json"


def _work(uid: str, recipe: dict):
    from app.publish import uploads
    from core.images import make_light
    try:
        src = uploads.path_of(uid)
        p = make_light(src, recipe)
        jpg, meta = _paths(uid, recipe)
        tmp = jpg.parent / (jpg.name + ".tmp")
        tmp.write_bytes(p.data)
        meta.write_text(json.dumps({"name": p.name, "w": p.width, "h": p.height}), encoding="utf-8")
        tmp.replace(jpg)
        export(uid, recipe)
    except Exception:
        pass   # не получилось заранее — посчитаем при публикации
    finally:
        with _lock:
            _threads.pop((uid, _key(recipe)), None)


def export(uid: str, recipe: dict) -> str | None:
    """Копия готовой лёгкой версии — в папку готовых файлов: «<имя оригинала>_light.jpg» (один раз на рецепт)."""
    from app.publish import outputs, uploads
    jpg, meta = _paths(uid, recipe)
    if not jpg.exists() or not meta.exists():
        return None
    m = json.loads(meta.read_text(encoding="utf-8"))
    if m.get("exported") and Path(m["exported"]).exists():   # скопировано ещё прежней версией программы
        return m["exported"]
    dst = outputs.save("jpeg", jpg, f"{uploads.path_of(uid).stem}_light.jpg", f"{uid}|{jpg.stem}")
    return str(dst) if dst else None


def ensure_async(uid: str, recipe: dict):
    """Запустить подготовку в фоне (если ещё не готово и не идёт). Готовую — докопировать в папку, если её задали позже."""
    jpg, _ = _paths(uid, recipe)
    if jpg.exists():
        try:
            export(uid, recipe)
        except Exception:
            pass
        return
    k = (uid, _key(recipe))
    with _lock:
        if k in _threads:
            return
        t = threading.Thread(target=_work, args=(uid, recipe), daemon=True, name=f"light:{uid}")
        _threads[k] = t
    t.start()


def status(uid: str, recipe: dict) -> str:
    jpg, _ = _paths(uid, recipe)
    if jpg.exists():
        return "ready"
    with _lock:
        return "working" if (uid, _key(recipe)) in _threads else "none"


def get_light(uid: str, path, recipe: dict, max_bytes=None, max_sum=None):
    """Готовая лёгкая версия, если влезает в лимиты; иначе — сделать под эти лимиты."""
    from core.images import Prepared, make_light
    with _lock:
        t = _threads.get((uid, _key(recipe)))
    if t is not None:
        t.join(timeout=300)   # уже готовится — дождёмся, а не будем считать второй раз
    jpg, meta = _paths(uid, recipe)
    if jpg.exists() and meta.exists():
        m = json.loads(meta.read_text(encoding="utf-8"))
        data = jpg.read_bytes()
        if (not max_bytes or len(data) <= max_bytes) and (not max_sum or m["w"] + m["h"] <= max_sum):
            return Prepared(m["name"], data, "image/jpeg", m["w"], m["h"], True, "лёгкая")
    return make_light(path, recipe, max_bytes=max_bytes, max_sum=max_sum)


def remove(uid: str):
    for pat in (f"{uid}.light-*", f"{uid}.jpg", f"{uid}.json"):   # второе — файлы старых версий
        for f in sub("uploads").glob(pat):
            f.unlink(missing_ok=True)
