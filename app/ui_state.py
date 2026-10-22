"""Что помнит окно между запусками: открытый экран и раздел, черновик поста, фильтры, тема, положение переключателей.

Раньше это лежало в памяти самого окна (localStorage), но окно при каждом запуске открывается на новом адресе
(порт выбирается заново) — и для него это новый сайт с пустой памятью. Теперь — файл ui.json в папке данных.
"""
import json
import threading

from app.paths import data_dir

_lock = threading.Lock()
MAX_BYTES = 2_000_000   # черновик с десятью файлами — несколько КБ; больше — что-то не так


def _path():
    return data_dir() / "ui.json"


def load() -> dict:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def update(changes: dict) -> dict:
    if not isinstance(changes, dict):
        raise ValueError("ожидался словарь")
    with _lock:
        d = load()
        for k, v in changes.items():
            if v is None:
                d.pop(str(k), None)
            else:
                d[str(k)] = v
        txt = json.dumps(d, ensure_ascii=False)
        if len(txt.encode("utf-8")) > MAX_BYTES:
            raise ValueError("слишком большое состояние окна")
        tmp = _path().with_name("ui.json.tmp")
        tmp.write_text(txt, encoding="utf-8")
        tmp.replace(_path())
    return d


def inline_js() -> str:
    """Для вставки в страницу: window.UI_STATE = {...} (без «</script>» внутри)."""
    return json.dumps(load(), ensure_ascii=False).replace("</", "<\\/")
