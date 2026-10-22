"""Discord: публикация через вебхук канала (бот не нужен)."""
import json
import re

import requests

from core.common import PlatformError, check_json

NAME = "Discord"
MAX_IMAGES = 10


def limits(c: dict) -> dict:
    # 10 МБ — лимит загрузки для сервера без бустов; с бустами можно поднять в config
    mb = c.get("max_file_mb", 10)
    return {"max_bytes": mb * 1024 * 1024, "formats": ("JPEG", "PNG", "GIF", "WEBP")}


_LABELED = re.compile(r"^(?P<label>[^\n:]{1,60}?)\s*:\s*<?(?P<url>https?://\S+?)>?\s*$")
_BARE = re.compile(r"(?<![(<\w])(https?://[^\s<>()]+)")


def short_links(text: str) -> str:
    """«Больше артов: https://t.me/x» → [Больше артов](<https://t.me/x>); голые ссылки — в <…> (без превью)."""
    out = []
    for line in (text or "").split("\n"):
        m = _LABELED.match(line.strip())
        if m and "](" not in line:
            out.append(f"[{m['label'].strip()}](<{m['url']}>)")
        else:
            out.append(_BARE.sub(r"<\1>", line) if "](" not in line else line)
    return "\n".join(out)


def post(c: dict, images, p) -> list[str]:
    hooks = c.get("webhook_urls") or [c.get("webhook_url")]
    hooks = [h["url"] if isinstance(h, dict) else h for h in hooks if h]
    if not hooks:
        raise PlatformError("Discord: не указан webhook_urls в config.toml")

    text = p.text
    if c.get("short_links", True):
        text = short_links(text)
    if p.title and c.get("include_title", True):
        text = f"**{p.title}**\n{text}".strip()

    # Для 18+ можно спрятать картинки под спойлер (размытие до клика)
    blur = p.mature and c.get("blur_nsfw", False)
    names = [("SPOILER_" + img.name) if blur else img.name for img in images]

    results = []
    for hook in hooks:
        payload = {
            "content": text[:2000],
            "attachments": [{"id": i, "filename": n} for i, n in enumerate(names)],
        }
        if c.get("no_previews", True):
            payload["flags"] = 4   # SUPPRESS_EMBEDS: убрать карточки ссылок, вложения остаются
        if c.get("username"):
            payload["username"] = c["username"]
        files = {f"files[{i}]": (names[i], img.data, img.mime) for i, img in enumerate(images)}
        r = requests.post(hook, params={"wait": "true"},
                          data={"payload_json": json.dumps(payload)}, files=files, timeout=120)
        msg = check_json(r, NAME)
        results.append(f"сообщение {msg.get('id')} в канале {msg.get('channel_id')}")
    return results
