"""Проверка новых версий: смотрим последний выпуск (Release) в репозитории на GitHub.

Сама программа не обновляется — окно только сообщает «вышла версия X» и открывает страницу выпуска.
Проверка — один раз при запуске (и по кнопке), её можно отключить. Запрос анонимный: работает, пока репозиторий публичный и в нём есть хотя бы один выпуск.
"""
import json
import re
import threading
import time

from app import __version__
from app.paths import data_dir

REPO = "Ehotxep/CrossPost"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
_lock = threading.Lock()
_busy = False
_asked = False   # при запуске спрашиваем один раз; дальше — только кнопкой «Проверить сейчас»


def _path():
    return data_dir() / "updates.json"


def _load() -> dict:
    try:
        d = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return {"check": d.get("check", True), "skip": d.get("skip", ""), "last": d.get("last", 0),
            "latest": d.get("latest"), "error": d.get("error", "")}


def _save(d: dict):
    try:
        _path().write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def parse(v: str) -> tuple:
    """'v0.2.1' → (0, 2, 1); всё нечисловое отбрасываем."""
    nums = re.findall(r"\d+", str(v or "").split("-")[0])
    return tuple(int(x) for x in nums[:4]) or (0,)


def newer(latest: str, current: str = __version__) -> bool:
    a, b = parse(latest), parse(current)
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def _fetch() -> dict:
    import requests
    r = requests.get(API, timeout=10, headers={"Accept": "application/vnd.github+json", "User-Agent": f"Crosspost/{__version__}"})
    if r.status_code == 404:
        raise ValueError("выпусков пока нет или репозиторий закрыт")
    if r.status_code in (403, 429):
        raise ValueError("GitHub временно ограничил запросы — попробуйте позже")
    r.raise_for_status()
    j = r.json()
    url = str(j.get("html_url") or "")
    if not url.startswith(f"https://github.com/{REPO}/"):
        url = f"https://github.com/{REPO}/releases"
    return {"version": str(j.get("tag_name") or "").lstrip("vV"), "name": j.get("name") or "", "url": url,
            "notes": (j.get("body") or "").strip()[:800], "date": (j.get("published_at") or "")[:10]}


def check(force: bool = False) -> dict:
    """Спросить GitHub (при запуске, если проверка включена, или по кнопке — force). Ошибки сети — в поле error, не исключением."""
    global _busy
    with _lock:
        d = _load()
        if not force and not d["check"]:
            return status()
        if _busy:
            return status()
        _busy = True
    try:
        try:
            d["latest"], d["error"] = _fetch(), ""
        except Exception as e:  # noqa: BLE001
            d["error"] = (str(e) if isinstance(e, ValueError) else "не удалось связаться с GitHub")[:160]
        d["last"] = time.time()
        _save(d)
    finally:
        _busy = False
    return status()


def check_async():
    """Вызывается при каждом обращении окна, но спрашивает GitHub только раз за запуск программы."""
    global _asked
    if _asked or _busy:
        return
    _asked = True
    if _load()["check"]:
        threading.Thread(target=check, daemon=True, name="updates").start()


def status() -> dict:
    d = _load()
    lt = d["latest"] or {}
    has = bool(lt.get("version")) and newer(lt["version"])
    return {"current": __version__, "check": d["check"], "last": d["last"], "error": d["error"], "checking": _busy,
            "latest": lt or None, "newer": has, "show": has and d["skip"] != lt.get("version"), "repo": REPO}


def set_check(on: bool) -> dict:
    d = _load()
    d["check"] = bool(on)
    _save(d)
    return status()


def skip(version: str) -> dict:
    d = _load()
    d["skip"] = str(version or "")
    _save(d)
    return status()
