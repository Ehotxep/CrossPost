"""e6ai.net — загрузка оригиналов (движок тот же, что у e621: /uploads.json, вход по логину и API-ключу).

Каждая картинка поста — отдельный пост на e6ai; вторая и следующие привязываются к первой как дочерние.
Рейтинг: SFW → s, Эротика → q, Откровенное → e. Источники — ссылки на этот же пост в других местах.
"""
import json
import os

import requests

from core.common import PlatformError

RATING = {"sfw": "s", "erotic": "q", "explicit": "e"}
MIN_TAGS = 4


def base(cfg) -> str:
    return os.environ.get("CROSSPOST_E6AI_BASE") or (cfg.get("base") or "https://e6ai.net").rstrip("/")


def name(cfg) -> str:
    """e6ai или e621 — куда сейчас загружаем (движок у них один)."""
    return cfg.get("site_name") or ("e621" if "e621.net" in base(cfg) else "e6ai")


def _ua(cfg) -> dict:
    from app import __version__
    return {"User-Agent": f"Crosspost/{__version__} (by {cfg.get('username') or 'unknown'} on {name(cfg)})"}


def check(cfg) -> dict:
    """Проверка логина и ключа: запрос, который требует входа."""
    if not cfg.get("username") or not cfg.get("api_key"):
        raise PlatformError(f"Укажите имя пользователя {name(cfg)} и API-ключ")
    r = requests.get(f"{base(cfg)}/favorites.json", params={"limit": 1}, auth=(cfg["username"], cfg["api_key"]),
                     headers=_ua(cfg), timeout=30)
    if r.status_code in (401, 403):
        raise PlatformError(f"{name(cfg)} не принял имя пользователя или API-ключ")
    if r.status_code >= 400:
        raise PlatformError(f"{name(cfg)} ответил {r.status_code}")
    u = requests.get(f"{base(cfg)}/users/{cfg['username']}.json", headers=_ua(cfg), timeout=30)
    info = u.json() if u.status_code == 200 else {}
    return {"name": info.get("name") or cfg["username"], "id": info.get("id"),
            "upload_limit": info.get("upload_limit"), "post_upload_count": info.get("post_upload_count")}


def clean_tags(tags) -> list[str]:
    out, seen = [], set()
    for t in tags:
        t = str(t).strip().lower().replace(" ", "_")
        if t and t not in seen and not t.startswith("rating:"):
            seen.add(t)
            out.append(t)
    return out


def bad_format(msg: str) -> bool:
    """e6ai не принял сам формат файла (а не теги или права)."""
    m = msg.lower()
    return any(w in m for w in ("file ext", "invalid file", "file type", "extension", "unsupported", "not supported", "mime"))


def upload(cfg, path, tags, rating: str, sources=(), description: str = "", parent_id=None, filename: str = "") -> dict:
    """Возвращает {"post_id", "url", "duplicate"}."""
    data = {"upload[tag_string]": " ".join(clean_tags(tags)), "upload[rating]": RATING[rating]}
    src = [s for s in sources if s][:10]
    if src:
        data["upload[source]"] = "\n".join(src)
    if description:
        data["upload[description]"] = description
    if parent_id:
        data["upload[parent_id]"] = str(parent_id)
    with open(path, "rb") as f:
        r = requests.post(f"{base(cfg)}/uploads.json", data=data, files={"upload[file]": (filename or os.path.basename(path), f)},
                          auth=(cfg["username"], cfg["api_key"]), headers=_ua(cfg), timeout=600)
    try:
        j = r.json()
    except ValueError:
        raise PlatformError(f"{name(cfg)}: HTTP {r.status_code}: {r.text[:200]}")
    if j.get("success"):
        pid = j.get("post_id")
        return {"post_id": pid, "url": f"{base(cfg)}/posts/{pid}", "duplicate": False}
    if j.get("reason") == "duplicate" and (j.get("post_id") or j.get("location")):
        pid = j.get("post_id") or str(j["location"]).rstrip("/").split("/")[-1]
        return {"post_id": pid, "url": f"{base(cfg)}/posts/{pid}", "duplicate": True}
    if r.status_code in (401, 403):
        raise PlatformError(f"{name(cfg)} не принял имя пользователя или API-ключ")
    msg = j.get("message") or j.get("reason") or json.dumps(j, ensure_ascii=False)[:200]
    raise PlatformError(f"{name(cfg)}: {msg}")
