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
            "latest": d.get("latest"), "error": d.get("error", ""),
            "channel": d.get("channel") if d.get("channel") in ("stable", "experimental") else "stable"}


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


def version_channel(version: str) -> str:
    return "experimental" if re.fullmatch(r"\d+(?:\.\d+)*-experimental\.\d+", str(version)) else "stable"


def eligible(version: str, channel: str, current: str | None = None) -> bool:
    current = __version__ if current is None else current
    if channel not in ("stable", "experimental") or version_channel(version) != channel:
        return False
    if version_channel(current) != channel:
        return True  # Explicit channel switch, including a return to an older stable build.
    if channel == "experimental":
        def order(value):
            base, build = value.split("-experimental.", 1)
            return (parse(base), int(build))
        return order(version) > order(current)
    return newer(version, current)


def release_version(release: dict, channel: str) -> str:
    if channel == "stable":
        if release.get("draft") or release.get("prerelease"):
            raise ValueError("Ожидался стабильный выпуск")
        version = str(release.get("tag_name") or "").lstrip("vV")
        if not re.fullmatch(r"\d+(?:\.\d+)*", version):
            raise ValueError("Неверная версия стабильного выпуска")
        return version
    tag = str(release.get("tag_name") or "")
    if release.get("draft") or not release.get("prerelease") or not re.fullmatch(r"experimental-\d+-\d+", tag):
        raise ValueError("Ожидалась экспериментальная сборка Crosspost")
    versions = [m.group(1) for asset in release.get("assets") or []
                if (m := re.fullmatch(r"Crosspost-(\d+(?:\.\d+)*-experimental\.\d+)\.zip", str(asset.get("name") or "")))]
    if len(versions) != 1:
        raise ValueError("В экспериментальном выпуске нет единственного архива Crosspost")
    return versions[0]


def fetch_release(channel: str) -> dict:
    import requests
    url = API if channel == "stable" else f"https://api.github.com/repos/{REPO}/releases?per_page=100"
    r = requests.get(url, timeout=15, headers={"Accept": "application/vnd.github+json", "User-Agent": f"Crosspost/{__version__}"})
    if r.status_code == 404:
        raise ValueError("выпусков пока нет или репозиторий закрыт")
    if r.status_code in (403, 429):
        raise ValueError("GitHub временно ограничил запросы — попробуйте позже")
    r.raise_for_status()
    data = r.json()
    if channel == "stable":
        release_version(data, channel)
        return data
    candidates = []
    for release in data:
        try:
            version = release_version(release, channel)
            base, build = version.split("-experimental.", 1)
            candidates.append(((parse(base), int(build)), release))
        except (ValueError, TypeError):
            continue
    if not candidates:
        raise ValueError("Экспериментальных сборок пока нет")
    return max(candidates, key=lambda item: item[0])[1]


def _fetch(channel: str = "stable") -> dict:
    j = fetch_release(channel)
    url = str(j.get("html_url") or "")
    if not url.startswith(f"https://github.com/{REPO}/"):
        url = f"https://github.com/{REPO}/releases"
    return {"version": release_version(j, channel), "name": j.get("name") or "", "url": url,
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
            d["latest"], d["error"] = _fetch(d["channel"]), ""
        except Exception as e:  # noqa: BLE001
            d["error"] = (str(e) if isinstance(e, ValueError) else "не удалось связаться с GitHub")[:160]
        d["last"] = time.time()
        if _load()["channel"] == d["channel"]:
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
    has = bool(lt.get("version")) and eligible(lt["version"], d["channel"])
    return {"current": __version__, "check": d["check"], "last": d["last"], "error": d["error"], "checking": _busy,
            "channel": d["channel"], "current_channel": version_channel(__version__), "latest": lt or None, "newer": has, "show": has and d["skip"] != lt.get("version"), "repo": REPO}


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


def set_channel(channel: str) -> dict:
    if channel not in ("stable", "experimental"):
        raise ValueError("Неизвестный канал обновлений")
    with _lock:
        if _busy:
            raise ValueError("Дождитесь завершения проверки обновлений")
        d = _load()
        if d["channel"] != channel:
            d.update(channel=channel, latest=None, error="", last=0, skip="")
            _save(d)
    return status()
