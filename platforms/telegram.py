"""Telegram: публикация через бота в каналы, PNG-оригиналы и пересылка в темы группы.

Для каждого канала можно выбрать, куда класть PNG-оригиналы (originals):
  "comments" — старая схема: файлами в комментарии к посту (в привязанную группу обсуждения).
               Бот ждёт, пока Telegram сам перешлёт пост в группу, и отвечает на эту копию.
  "reply"    — новая схема: файлами ответом на пост прямо в канале.
  "none"     — не отправлять.

Пост всегда уходит лёгким JPEG (рецепт в [light]). 18+ пост — под спойлером.
Дополнительно пост можно переслать в тему группы ([telegram.group]): SFW — в одну тему, 18+ — в другую.

Бот должен быть админом во всех каналах, в группах обсуждения (для "comments") и в группе с темами.
"""
import json
import time
from dataclasses import dataclass, field

import requests

from core.common import PlatformError
from core.images import Prepared, load_original, make_light

NAME = "Telegram"
MAX_IMAGES = 10
CAPTION_LIMIT = 1024
PHOTO_MAX_BYTES = 10 * 1024 * 1024   # лимит фото у Bot API
PHOTO_MAX_SUM = 10000                # сумма ширины и высоты фото
BOT_FILE_LIMIT = 50 * 1024 * 1024    # лимит загрузки файла ботом
MODES = ("comments", "reply", "none")


@dataclass
class TgMedia:
    photos: list[Prepared]
    originals: list[Prepared] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def describe_lines(self) -> list[str]:
        lines = ["пост: " + "; ".join(p.describe() for p in self.photos)]
        if self.originals:
            lines.append("оригиналы: " + "; ".join(p.describe() for p in self.originals))
        lines += [f"! {n}" for n in self.notes]
        return lines


# --- Настройки ----------------------------------------------------------------

def channels_of(c: dict) -> list[dict]:
    """Каналы из конфига: новый формат channels = [{id, rating, originals}], старый — chats."""
    out = []
    for ch in c.get("channels") or c.get("chats") or []:
        out.append(dict(ch) if isinstance(ch, dict) else {"id": ch})
    return out


def originals_mode(ch: dict, c: dict) -> str:
    mode = str(ch.get("originals", c.get("originals", "comments"))).lower()
    if mode not in MODES:
        raise PlatformError(f"Telegram: originals = {mode!r} у {ch.get('id')} (можно: comments, reply, none)")
    return mode


def prepare_images(c: dict, paths) -> TgMedia:
    photos = [make_light(p, c.get("_light"), max_bytes=PHOTO_MAX_BYTES, max_sum=PHOTO_MAX_SUM) for p in paths]
    media = TgMedia(photos)
    if any(originals_mode(ch, c) != "none" for ch in channels_of(c)):
        for p in paths:
            try:
                media.originals.append(load_original(p, BOT_FILE_LIMIT))
            except ValueError as e:
                media.notes.append(f"{e} — этот оригинал не будет отправлен")
    return media


def plan_lines(c: dict, p) -> list[str]:
    """Что произойдёт при публикации — для предпросмотра плана (--dry-run и подтверждения)."""
    labels = {"comments": "оригиналы в комментарии", "reply": "оригиналы ответом на пост", "none": "без оригиналов"}
    lines = [f"{ch['id']}: {labels[originals_mode(ch, c)]}" for ch in channels_of(c)]
    if p.mature and c.get("spoiler_nsfw", True):
        lines.append("18+: картинки под спойлером")
    group = c.get("group") or {}
    if group.get("id"):
        topic = group.get("nsfw_topic") if p.mature else group.get("sfw_topic")
        if topic:
            lines.append(f"пересылка в группу, тема {topic} ({'18+' if p.mature else 'SFW'})")
        else:
            lines.append("в группу не пересылается: тема для этого рейтинга не указана")
    return lines


# --- Bot API ------------------------------------------------------------------

def _call(token, method, data=None, files=None, timeout=120):
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", data=data, files=files, timeout=timeout)
    try:
        j = r.json()
    except ValueError:
        raise PlatformError(f"Telegram: HTTP {r.status_code}: {r.text[:300]}")
    if not j.get("ok"):
        desc = j.get("description") or ""
        hint = ""
        if "can't be forwarded" in desc or "protected" in desc:
            hint = " — в канале включён запрет копирования, пересылка невозможна"
        elif "webhook" in desc.lower():
            hint = " — у бота включён webhook; для режима comments он должен быть выключен (deleteWebhook)"
        elif "not enough rights" in desc or "not a member" in desc or "chat not found" in desc:
            hint = " — проверьте, что бот добавлен админом с правом публикации"
        raise PlatformError(f"Telegram ({method}): {desc}{hint}")
    return j["result"]


def _link(msg) -> str:
    chat = msg.get("chat", {})
    if chat.get("username"):
        return f"https://t.me/{chat['username']}/{msg['message_id']}"
    cid = str(chat.get("id", ""))
    if cid.startswith("-100"):
        return f"https://t.me/c/{cid[4:]}/{msg['message_id']}"
    return f"сообщение {msg['message_id']} в {chat.get('title') or cid}"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _reply(message_id: int) -> str:
    return json.dumps({"message_id": message_id, "allow_sending_without_reply": True})


def _send_photos(token, chat, photos, caption, parse_mode, spoiler) -> list[dict]:
    if len(photos) == 1:
        img = photos[0]
        data = {"chat_id": chat, "caption": caption}
        if parse_mode and caption:
            data["parse_mode"] = parse_mode
        if spoiler:
            data["has_spoiler"] = "true"
        return [_call(token, "sendPhoto", data, {"photo": (img.name, img.data, img.mime)})]
    media = []
    for i, img in enumerate(photos):
        item = {"type": "photo", "media": f"attach://f{i}"}
        if spoiler:
            item["has_spoiler"] = True
        if i == 0 and caption:
            item["caption"] = caption
            if parse_mode:
                item["parse_mode"] = parse_mode
        media.append(item)
    files = {f"f{i}": (img.name, img.data, img.mime) for i, img in enumerate(photos)}
    return _call(token, "sendMediaGroup", {"chat_id": chat, "media": json.dumps(media)}, files)


def _send_files(token, chat, files_list, reply_to: int, silent: bool, thread_id=None) -> None:
    """Оригиналы файлами (без сжатия и без подписи) ответом на сообщение."""
    base = {"chat_id": chat, "reply_parameters": _reply(reply_to)}
    if silent:
        base["disable_notification"] = "true"
    if thread_id:
        base["message_thread_id"] = thread_id
    for start in range(0, len(files_list), 10):  # в одном альбоме не больше 10 файлов
        chunk = files_list[start:start + 10]
        if len(chunk) == 1:
            f = chunk[0]
            _call(token, "sendDocument", base, {"document": (f.name, f.data, f.mime)}, timeout=600)
        else:
            media = [{"type": "document", "media": f"attach://d{i}"} for i in range(len(chunk))]
            files = {f"d{i}": (f.name, f.data, f.mime) for i, f in enumerate(chunk)}
            _call(token, "sendMediaGroup", {**base, "media": json.dumps(media)}, files, timeout=600)


def _origin(m: dict) -> tuple:
    o = m.get("forward_origin") or {}
    chat_id = (o.get("chat") or {}).get("id") or (m.get("forward_from_chat") or {}).get("id")
    msg_id = o.get("message_id") or m.get("forward_from_message_id")
    return chat_id, msg_id


def _wait_discussion_copy(token, group_id, channel_chat_id, channel_msg_id, timeout: float):
    """Ждёт, пока Telegram перешлёт пост канала в группу обсуждения, и возвращает эту копию.

    Используется getUpdates: этот токен не должен параллельно обслуживать другого бота.
    """
    deadline = time.monotonic() + timeout
    offset = None
    found = None
    while time.monotonic() < deadline and not found:
        data = {"timeout": 5, "allowed_updates": json.dumps(["message"])}
        if offset is not None:
            data["offset"] = offset
        for u in _call(token, "getUpdates", data, timeout=30):
            offset = u["update_id"] + 1
            m = u.get("message") or {}
            if (m.get("is_automatic_forward") and (m.get("chat") or {}).get("id") == group_id
                    and _origin(m) == (channel_chat_id, channel_msg_id)):
                found = m
    if offset is not None:  # подтверждаем прочитанные обновления
        try:
            _call(token, "getUpdates", {"offset": offset, "timeout": 0}, timeout=30)
        except PlatformError:
            pass
    return found


# --- Публикация ----------------------------------------------------------------

def post(c: dict, media: TgMedia, p) -> list[str]:
    token = c.get("bot_token")
    channels = channels_of(c)
    group = c.get("group") or {}
    if not token:
        raise PlatformError("Telegram: укажите bot_token в config.toml")
    if not channels and not group.get("id"):
        raise PlatformError("Telegram: нет каналов для публикации")

    text = p.text
    if p.title and c.get("include_title", True):
        text = f"<b>{_esc(p.title)}</b>\n{_esc(text)}".strip()
        parse_mode = "HTML"
    else:
        parse_mode = None
    long_text = len(text) > CAPTION_LIMIT
    caption = "" if long_text else text
    spoiler = bool(p.mature and c.get("spoiler_nsfw", True))
    silent = c.get("silent_originals", True)
    wait = float(c.get("comments_wait_seconds", 60))

    # errors — пост не вышел (можно повторить); warnings — пост вышел, но что-то после него не удалось
    # (повторять публикацию нельзя — будет дубль, недостающее добавляется вручную).
    results, errors, published = [], [], []
    for ch in channels:
        chat = ch["id"]
        try:
            if long_text:  # длинный текст — отдельным сообщением перед картинками
                _call(token, "sendMessage", {"chat_id": chat, "text": text[:4096],
                                             **({"parse_mode": parse_mode} if parse_mode else {})})
            msgs = _send_photos(token, chat, media.photos, caption, parse_mode, spoiler)
        except PlatformError as e:
            errors.append(f"{chat}: {e}")
            continue
        first = msgs[0]
        published.append((ch, first["chat"]["id"], [m["message_id"] for m in msgs]))
        line = _link(first)
        if media.originals:
            line += _send_originals(token, c, ch, chat, first, media.originals, silent, wait)
        results.append(line)

    # Пересылка в тему группы: SFW — из SFW-канала, 18+ — из 18+ канала
    if group.get("id") and published:
        topic = group.get("nsfw_topic") if p.mature else group.get("sfw_topic")
        if topic or not group.get("topics_required", True):
            want = "nsfw" if p.mature else "sfw"
            src = next((x for x in published if str(x[0].get("rating", "all")).lower() == want), published[0])
            data = {"chat_id": group["id"], "from_chat_id": src[1], "message_ids": json.dumps(src[2])}
            if topic:
                data["message_thread_id"] = topic
            try:
                _call(token, "forwardMessages", data)
                results.append(f"переслано в группу{' (тема ' + str(topic) + ')' if topic else ''}")
            except PlatformError as e:
                results.append(f"⚠ пост вышел, но пересылка в группу не удалась: {e} — перешлите вручную")

    if errors:
        raise PlatformError("Telegram: " + "; ".join(errors) + (f" (успешно: {', '.join(results)})" if results else ""))
    return results


def _send_originals(token, c, ch, chat, first, originals, silent, wait) -> str:
    """Кладёт PNG-оригиналы по режиму канала. Возвращает хвост строки отчёта; не бросает исключений."""
    try:
        mode = originals_mode(ch, c)
        if mode == "none":
            return ""
        if mode == "comments":
            linked = _call(token, "getChat", {"chat_id": chat}).get("linked_chat_id")
            if linked:
                copy = _wait_discussion_copy(token, linked, first["chat"]["id"], first["message_id"], wait)
                if not copy:
                    return (f" · ⚠ копия поста не появилась в обсуждении за {wait:.0f} с — "
                            f"PNG не отправлены, добавьте в комментарии вручную")
                _send_files(token, linked, originals, copy["message_id"], silent)
                return f" · {len(originals)} PNG в комментариях"
            prefix = " · у канала нет комментариев, оригиналы — ответом в канале"
        else:
            prefix = ""
        _send_files(token, chat, originals, first["message_id"], silent)
        return f"{prefix} · {len(originals)} PNG ответом на пост"
    except PlatformError as e:
        return f" · ⚠ пост вышел, но PNG не отправлены: {e} — добавьте вручную"
