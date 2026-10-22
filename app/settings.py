"""Настройки приложения в settings.json (в папке данных)."""
import copy
import json
import threading

from app.paths import data_dir

DEFAULTS = {
    # Ник на e621/e6ai — по правилам сайтов он должен быть в User-Agent запросов
    "nickname": "",
    # Теги «моего стиля» для трендов. Позже будут заполняться теггером по вашим работам.
    "style_tags": [],
    # Теги, скрытые из трендов (детали вроде collarbone, nails, которые не интересны как темы)
    "hidden_tags": [],
    # сколько новостей по свежим артам: few — только хиты, normal — хиты и «необычно много реакций», many — ещё и «лучше обычного»
    "news_volume": "normal",
    # подбирать теги на процессоре, если видеокарта не используется (медленно — только по согласию)
    "tagger_cpu_ok": False,
    "trends": {
        "e6ai": {"enabled": True, "refresh_days": 1},
        "e621": {"enabled": True, "refresh_days": 7},
    },
}

_lock = threading.Lock()


def _path():
    return data_dir() / "settings.json"


def _merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> dict:
    with _lock:
        p = _path()
        user = {}
        if p.exists():
            try:
                user = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                user = {}
        return _merge(DEFAULTS, user)


def save(changes: dict) -> dict:
    current = load()
    new = _merge(current, changes)
    for key in ("style_tags", "hidden_tags"):  # списки заменяем целиком, а не сливаем
        if key in changes:
            new[key] = [t.strip().lower().replace(" ", "_") for t in changes[key] if t.strip()]
    with _lock:
        _path().write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    return new
