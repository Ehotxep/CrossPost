"""Общий e621ng-адаптер: blep.art, e621 и floofgens.net.

Оригиналы загружаются через /uploads.json с собственным аккаунтом выбранного сайта.
Каждая картинка — отдельный пост; следующие привязываются к первой как дочерние.
"""
import hashlib
import json
import os

import requests

from core.common import PlatformError, PublicationUnknown

RATING = {"sfw": "s", "erotic": "q", "explicit": "e"}
MIN_TAGS = 4


def base(cfg) -> str:
    return os.environ.get("CROSSPOST_E6AI_BASE") or (cfg.get("base") or "https://blep.art").rstrip("/")


def history_site(cfg) -> str:
    from app.publish import settings as PS
    key = cfg.get("site_name") or PS.booru_site_for_url(base(cfg)) or "e6ai"
    return "blep" if key in ("e6ai", "blep") else key


def name(cfg) -> str:
    from app.publish import settings as PS
    key = history_site(cfg)
    return PS.BOORU_SITES["e6ai" if key == "blep" else key]["name"]


def _ua(cfg) -> dict:
    from app import __version__
    return {"User-Agent": f"Crosspost/{__version__} (by {cfg.get('username') or 'unknown'} on {name(cfg)})"}


def check(cfg) -> dict:
    """Проверка логина и ключа: запрос, который требует входа."""
    if not cfg.get("username") or not cfg.get("api_key"):
        raise PlatformError(f"Укажите имя пользователя {name(cfg)} и API-ключ")
    r = requests.get(f"{base(cfg)}/favorites.json", params={"limit": 1}, auth=(cfg["username"], cfg["api_key"]),
                     headers=_ua(cfg), timeout=30, allow_redirects=False)
    if r.status_code in (401, 403):
        raise PlatformError(f"{name(cfg)} не принял имя пользователя или API-ключ")
    if not 200 <= r.status_code < 300:
        raise PlatformError(f"{name(cfg)} ответил {r.status_code}")
    u = requests.get(f"{base(cfg)}/users/{cfg['username']}.json", headers=_ua(cfg), timeout=30, allow_redirects=False)
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


def _find_uploaded_file(cfg, digest):
    """One read-only reconciliation after a lost response; never repeat the POST."""
    try:
        response = requests.get(f"{base(cfg)}/posts.json", params={"tags": f"md5:{digest}", "limit": 2},
                                auth=(cfg["username"], cfg["api_key"]), headers=_ua(cfg),
                                timeout=(5, 15), allow_redirects=False)
        if response.status_code != 200:
            return None
        data = response.json()
        posts = data.get('posts') if isinstance(data, dict) else data
        if not isinstance(posts, list):
            return None
        for post in posts:
            if not isinstance(post, dict):
                continue
            file = post.get('file')
            pid = post.get('id')
            flags = post.get('flags') or {}
            if (isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
                    and isinstance(file, dict) and file.get('md5') == digest
                    and isinstance(flags, dict) and not flags.get('deleted')):
                return {"post_id": pid, "url": f"{base(cfg)}/posts/{pid}", "duplicate": True, "recovered": True}
    except (requests.RequestException, ValueError, TypeError):
        pass
    return None


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
    # Freeze a fingerprint of the exact bytes passed to requests. A later search
    # cannot confirm a different file or a post returned by a loose search match.
    with open(path, "rb") as f:
        digest = hashlib.md5(usedforsecurity=False)
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
        digest = digest.hexdigest()
        f.seek(0)
        from core import retry   # После обрыва не повторяем отправку автоматически.
        try:
            r = retry.post(f"{base(cfg)}/uploads.json", idempotent=False, what=f"{name(cfg)}: публикация", data=data,
                           files={"upload[file]": (filename or os.path.basename(path), f)},
                           auth=(cfg["username"], cfg["api_key"]), headers=_ua(cfg), timeout=600, allow_redirects=False)
        except PublicationUnknown as error:
            recovered = _find_uploaded_file(cfg, digest)
            if recovered:
                return recovered
            raise PublicationUnknown(f"{error} Проверка по хешу файла не подтвердила публикацию; результат остаётся неизвестным.") from error
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
