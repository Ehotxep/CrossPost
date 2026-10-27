"""ВКонтакте: пост на стену группы от имени группы.

Загрузка фото на стену работает только с токеном пользователя (админа группы),
токен сообщества для этого не подходит. Токен получается командой `python auth.py vk`
и обновляется автоматически.
"""
import secrets
import time

import requests

from core.common import PlatformError, check_json, load_token, save_token

NAME = "VK"
MAX_IMAGES = 10
API = "https://api.vk.com/method/"
V = "5.199"


def _token(c: dict) -> str:
    if c.get("access_token"):  # вариант «вставил токен руками», без автообновления
        return c["access_token"]
    t = load_token("vk")
    if not t:
        raise PlatformError("VK: нет токена. Запустите: python auth.py vk")
    if time.time() < t.get("expires_at", 0) - 60:
        return t["access_token"]
    r = requests.post("https://id.vk.com/oauth2/auth", data={
        "grant_type": "refresh_token",
        "refresh_token": t["refresh_token"],
        "client_id": c["app_id"],
        "device_id": t["device_id"],
        "state": secrets.token_urlsafe(16),
    }, timeout=30)
    j = check_json(r, NAME)
    if "access_token" not in j:
        raise PlatformError(f"VK: не удалось обновить токен ({j}). Запустите: python auth.py vk")
    save_token("vk", {
        "access_token": j["access_token"],
        "refresh_token": j.get("refresh_token", t["refresh_token"]),
        "device_id": t["device_id"],
        "expires_at": time.time() + int(j.get("expires_in", 3600)),
        "scope": j.get("scope", t.get("scope", "")),
    })
    return j["access_token"]


def _call(token, method, **params):
    from core import retry
    # wall.post создаёт запись — после обрыва её нельзя слать второй раз; остальные методы (получить адрес, сохранить фото) безопасны
    r = retry.post(API + method, idempotent=method != "wall.post", what=f"ВК ({method})",
                   data={**params, "access_token": token, "v": V}, timeout=60)
    j = check_json(r, NAME)
    if "error" in j:
        raise PlatformError(f"VK ({method}): {j['error'].get('error_msg')}")
    return j["response"]


def post(c: dict, images, p, progress=None) -> list[str]:
    gid = abs(int(c["group_id"]))
    token = _token(c)

    attachments = []
    for i, img in enumerate(images):
        if progress:
            progress(i / (len(images) + 1))
        srv = _call(token, "photos.getWallUploadServer", group_id=gid)
        from core import retry
        up = retry.post(srv["upload_url"], idempotent=True, what=f"ВК: загрузка {img.name}",
                        files={"photo": (img.name, img.data, img.mime)}, timeout=300).json()
        if not up.get("photo") or up["photo"] == "[]":
            raise PlatformError(f"VK: сервер не принял файл {img.name}: {up}")
        saved = _call(token, "photos.saveWallPhoto", group_id=gid,
                      photo=up["photo"], server=up["server"], hash=up["hash"])[0]
        attachments.append(f"photo{saved['owner_id']}_{saved['id']}")

    text = p.text
    if p.title and c.get("include_title", True):
        text = f"{p.title}\n\n{text}".strip()
    res = _call(token, "wall.post", owner_id=-gid, from_group=1,
                message=text, attachments=",".join(attachments))
    return [f"https://vk.com/wall-{gid}_{res['post_id']}"]


def post_media(c: dict, path, kind: str, p, progress=None) -> list[str]:
    """GIF — документом (ВК проигрывает его в ленте), видео — видеозаписью группы. Файл как есть: ВК сам перекодирует."""
    from pathlib import Path
    gid = abs(int(c["group_id"]))
    token = _token(c)
    path = Path(path)
    text = p.text
    if p.title and c.get("include_title", True):
        text = f"{p.title}\n\n{text}".strip()
    if progress:
        progress(0.05)
    with open(path, "rb") as fh:
        if kind == "gif":
            srv = _call(token, "docs.getWallUploadServer", group_id=gid)
            from core import retry
            up = retry.post(srv["upload_url"], idempotent=True, what="ВК: загрузка GIF", files={"file": (path.name, fh, "image/gif")}, timeout=900).json()
            if not up.get("file"):
                raise PlatformError(f"VK: сервер не принял GIF: {up}")
            saved = _call(token, "docs.save", file=up["file"], title=p.title or path.stem)
            doc = saved.get("doc") or (saved[0] if isinstance(saved, list) else saved)
            att = f"doc{doc['owner_id']}_{doc['id']}"
        else:
            sv = _call(token, "video.save", group_id=gid, name=(p.title or path.stem)[:128], wallpost=0, is_private=0)
            from core import retry
            up = retry.post(sv["upload_url"], idempotent=True, what="ВК: загрузка видео", files={"video_file": (path.name, fh, "video/mp4")}, timeout=3600)
            j = check_json(up, NAME)
            if "error" in j:
                raise PlatformError(f"VK: сервер не принял видео: {j['error']}")
            att = f"video{sv.get('owner_id', -gid)}_{sv.get('video_id') or j.get('video_id')}"
    if progress:
        progress(0.85)
    res = _call(token, "wall.post", owner_id=-gid, from_group=1, message=text, attachments=att)
    return [f"https://vk.com/wall-{gid}_{res['post_id']}"]
