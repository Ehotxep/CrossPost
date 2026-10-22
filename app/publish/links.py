"""Ссылки в подписи и тексте: пишутся как [Текст](https://…), каждая площадка оформляет их по-своему.

  Telegram — кликабельное слово (HTML <a>);
  Discord  — кликабельное слово [Текст](<url>) (угловые скобки — без карточки-превью);
  ВК       — в постах сообщества нельзя спрятать ссылку под слово, поэтому «Текст: https://…».
"""
import html
import re

LINK = re.compile(r"\[([^\]\n]+)\]\(<?(https?://[^\s<>()]+)>?\)")


def telegram_html(text: str) -> str:
    out, pos = [], 0
    for m in LINK.finditer(text or ""):
        out.append(html.escape(text[pos:m.start()], quote=False))
        out.append(f'<a href="{html.escape(m[2], quote=True)}">{html.escape(m[1], quote=False)}</a>')
        pos = m.end()
    out.append(html.escape((text or "")[pos:], quote=False))
    return "".join(out)


def discord(text: str) -> str:
    return LINK.sub(lambda m: f"[{m[1]}](<{m[2]}>)", text or "")


def plain(text: str) -> str:
    return LINK.sub(lambda m: f"{m[1]}: {m[2]}", text or "")


def for_platform(text: str, platform: str) -> str:
    if platform == "discord":
        return discord(text)
    if platform in ("vk", "deviantart"):
        return plain(text)
    return text   # Telegram превращает в HTML при отправке (там же экранируется остальной текст)
