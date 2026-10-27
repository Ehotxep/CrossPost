"""Discord: публикация через вебхук канала (бот не нужен)."""
import json
import re

from core.common import PlatformError, check_json

NAME = "Discord"
MAX_IMAGES = 10


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
        raise PlatformError("Discord: не указан вебхук — добавьте его в «Настройки» → Discord")

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
        from core import retry
        r = retry.post(hook, idempotent=False, what="Discord", params={"wait": "true"},
                       data={"payload_json": json.dumps(payload)}, files=files, timeout=300)
        msg = check_json(r, NAME)
        results.append(f"сообщение {msg.get('id')} в канале {msg.get('channel_id')}")
    return results
